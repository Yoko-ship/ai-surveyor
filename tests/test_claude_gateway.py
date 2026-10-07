"""Изолированные офлайн-проверки шлюза Claude; subprocess CLI и внешний HTTP замоканы."""
import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import claude_gateway as gateway, chatgpt_plan as plan, claude_runtime as runtime, llm
from tools.claude_gateway import DualStackServer, handler, environment_config


def events(*items):
    return "\n".join(json.dumps(x) for x in items)


def assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


DONE = {"type": "result", "subtype": "success", "is_error": False, "result": "yes",
        "usage": {"input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 1}}


class GatewayClientTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CLAUDE_GATEWAY_ENABLED": "1", "CODEX_GATEWAY_ENABLED": "1",
            "CHATGPT_PLAN_ENABLED": "0", "CLAUDE_GATEWAY_URL": "http://claude-gateway.railway.internal:8787",
            "CLAUDE_GATEWAY_TOKEN": "test-only-" * 8, "CLAUDE_GATEWAY_MODEL": "test-model",
            "CHATGPT_PLAN_TESTER_IDS": "123"})
        self.env.start()
        self.mark = plan.actor.set("123")

    def tearDown(self):
        plan.actor.reset(self.mark)
        self.env.stop()

    def test_claude_takes_priority_and_allowlist_applies(self):
        self.assertEqual(llm.provider(), "claude_gateway")
        self.assertTrue(gateway.ready())
        self.assertTrue(llm.supports_files())
        with patch.dict(os.environ, {"CHATGPT_PLAN_TESTER_IDS": ""}):
            self.assertFalse(gateway.ready())
            with patch.object(gateway.urllib.request, "build_opener") as network:
                with self.assertRaises(plan.PlanError):
                    gateway.request([], [])
                network.assert_not_called()
        with patch.dict(os.environ, {"CLAUDE_GATEWAY_ENABLED": "0"}):
            self.assertEqual(llm.provider(), "codex_gateway")

    def test_endpoint_only_https_or_private_railway_network(self):
        for url in ("https://gateway.test", "http://claude-gateway.railway.internal:8787"):
            with patch.dict(os.environ, {"CLAUDE_GATEWAY_URL": url}):
                self.assertTrue(gateway.ready())
        for url in ("http://gateway.test", "http://railway.internal.evil.test", "https://secret@gateway.test",
                    "https://gateway.test/a", "https://gateway.test?token=x", ""):
            with patch.dict(os.environ, {"CLAUDE_GATEWAY_URL": url}):
                self.assertFalse(gateway.ready())

    def test_integration_redacts_text_and_status_hides_connection(self):
        with patch.object(llm, "get", return_value="test"), patch.object(llm, "_log_call"), \
             patch.object(gateway.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps({"ok": True, "text": "answer"}).encode())
            result = llm.chat_raw("test", [{"role": "user", "content": "mail@example.com"}], web_search=True)
            self.assertTrue(result["ok"])
            req = opener.return_value.open.call_args.args[0]
            payload = json.loads(req.data)
            self.assertEqual(payload["tester_id"], "123")
            self.assertTrue(payload["web_search"])
            self.assertNotIn("mail@example.com", req.data.decode())
            self.assertEqual(req.full_url, "http://claude-gateway.railway.internal:8787/infer")
            status = llm.status()
            self.assertEqual(status["provider"], "claude_gateway")
            self.assertEqual(status["base_url"], "")
            self.assertEqual(status["key_mask"], "")

    def test_failure_no_retry_no_paid_fallback(self):
        with patch.object(gateway, "request", side_effect=plan.PlanError("offline")) as call, \
             patch.object(llm, "_log_call"), patch.object(llm, "_request") as paid:
            self.assertFalse(llm.chat_raw("test", [{"role": "user", "content": "test"}], retries=4)["ok"])
            self.assertEqual(call.call_count, 1)
            paid.assert_not_called()

    def test_http_error_body_not_exposed(self):
        with patch.object(gateway.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError("https://x", 502, "private", {}, None)
            with self.assertRaises(plan.PlanError) as caught:
                gateway.request([], [])
            self.assertNotIn("private", str(caught.exception))


class RuntimeTests(unittest.TestCase):
    def test_only_search_tools_and_only_when_enabled(self):
        search = assistant({"type": "tool_use", "name": "WebSearch", "input": {}})
        with self.assertRaises(runtime.RuntimeFailure):
            runtime.parse_events(events(search, DONE))
        self.assertEqual(runtime.parse_events(events(search, DONE), True)["web_search"],
                         {"enabled": True, "calls": 1})
        for tool in ("Bash", "Read", "Write", "mcp__x__y", "Task"):
            bad = assistant({"type": "tool_use", "name": tool, "input": {}})
            with self.assertRaises(runtime.RuntimeFailure):
                runtime.parse_events(events(bad, DONE), True)

    def test_only_successful_final_answer(self):
        result = runtime.parse_events(events(assistant({"type": "text", "text": "yes"}), DONE))
        self.assertEqual(result["text"], "yes")
        self.assertEqual(result["usage"], {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6})
        for stream in (events(assistant({"type": "text", "text": "yes"})), events({**DONE, "is_error": True}),
                       events({**DONE, "subtype": "error_max_turns"}), events({**DONE, "result": " "})):
            with self.assertRaises(runtime.RuntimeFailure):
                runtime.parse_events(stream)

    def test_rate_limit_reading(self):
        info = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.25, "resetsAt": 1},
                                                       "seven_day": {"utilization": 0.5}}}
        runtime.parse_events(events({"type": "rate_limit_event", "rate_limit_info": info}, DONE))
        self.assertEqual(runtime.rate_limit()["five_hour_used_percent"], 25.0)
        self.assertEqual(runtime.rate_limit()["seven_day_used_percent"], 50.0)

    def test_command_isolated_from_settings_and_tools(self):
        cmd = runtime.command("/test/claude", "test-model", "backup", "low", Path("/tmp/i.txt"))
        for required in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(required, cmd)
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--fallback-model") + 1], "backup")
        self.assertNotIn("--allowedTools", cmd)
        search = runtime.command("/test/claude", "test-model", "test-model", "low", Path("/tmp/i.txt"), True)
        self.assertEqual(search[search.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertEqual(search[search.index("--allowedTools") + 1:], ["WebSearch", "WebFetch(domain:lex.uz)"])
        self.assertNotIn("--fallback-model", search)

    def test_environment_never_forwards_api_key_or_gateway_secret(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x", "CLAUDE_GATEWAY_TOKEN": "y",
                                    "TELEGRAM_BOT_TOKEN": "z", "CLAUDE_CODE_OAUTH_TOKEN": "ok"}):
            env = runtime.environment()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_GATEWAY_TOKEN", env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "ok")
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")

    def test_attachments_become_blocks_and_reject_truncation(self):
        import pymupdf as fitz
        def part(mime, data):
            return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}
        with fitz.open() as doc:
            doc.new_page().insert_text((30, 30), "TEST")
            png = doc[0].get_pixmap().tobytes("png")
            blocks = runtime.content([part("image/png", png), part("application/pdf", doc.tobytes())])
            self.assertEqual([b["type"] for b in blocks], ["image", "document"])
            self.assertEqual(blocks[0]["source"]["media_type"], "image/jpeg")
            for _ in range(12):
                doc.new_page()
            with self.assertRaises(ValueError):
                runtime.content([part("application/pdf", doc.tobytes())])
            with self.assertRaises(ValueError):
                runtime.content([part("text/html", b"test")])

    def test_files_disable_search_and_secret_not_in_prompt(self):
        captured = {}
        class Proc:
            pid, returncode = 1, 0
            def __init__(self, cmd, stdin, stdout, **kw):
                captured.update(cmd=cmd, env=kw["env"], out=stdout)
            def communicate(self, data, timeout):
                captured["stdin"] = data.decode()
                captured["out"].write(events(DONE).encode())
        import pymupdf as fitz
        with fitz.open() as doc:
            doc.new_page()
            pdf = base64.b64encode(doc.tobytes()).decode()
        secret = "test-private-gateway-credential"
        payload = {"messages": [{"role": "user", "content": "q " + secret}], "web_search": True,
                   "files": [{"inline_data": {"mime_type": "application/pdf", "data": pdf}}]}
        with patch.object(runtime.subprocess, "Popen", Proc), patch.dict(os.environ, {"CLAUDE_GATEWAY_TOKEN": secret}):
            result = runtime.infer(payload, "/test/claude", "test-model")
        self.assertEqual(result["web_search"], {"enabled": False, "calls": 0})
        self.assertEqual(captured["cmd"][captured["cmd"].index("--tools") + 1], "")
        self.assertNotIn(secret, captured["stdin"])
        self.assertNotIn("CLAUDE_GATEWAY_TOKEN", captured["env"])
        self.assertEqual(json.loads(captured["stdin"])["message"]["content"][0]["type"], "document")


class HttpGatewayTests(unittest.TestCase):
    def setUp(self):
        self.token = "test-only-token-" * 5
        self.server = DualStackServer(("::1", 0), handler({"token": self.token, "tester_ids": ["123"],
            "model": "test-model", "fallback_model": "", "effort": "low", "claude_binary": "/test/claude",
            "slots": 1}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.url = "http://[::1]:" + str(self.port)
        self.login = patch.object(runtime, "auth_status", return_value={"logged_in": True, "auth_method": "test"})
        self.login.start()

    def tearDown(self):
        self.login.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, payload=None, token=True, path="/infer"):
        headers = {"Authorization": "Bearer " + self.token} if token else {}
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, headers=headers, data=data)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_auth_allowlist_and_model_before_inference(self):
        with patch("tools.claude_gateway.infer", return_value={"ok": True, "text": "ok"}) as infer:
            good = {"tester_id": "123", "model": "test-model", "messages": []}
            self.assertEqual(self.call(good, token=False)[0], 401)
            self.assertEqual(self.call({**good, "tester_id": "456"})[0], 403)
            self.assertEqual(self.call({**good, "model": "other"})[0], 400)
            infer.assert_not_called()
            self.assertEqual(self.call(good)[0], 200)
            self.assertEqual(infer.call_count, 1)
            code, health = self.call(path="/health")
            self.assertEqual((code, health["logged_in"], health["model"]), (200, True, "test-model"))
            self.assertEqual(self.call(path="/health", token=False)[0], 401)
            self.assertEqual(self.call(path="/livez", token=False), (200, {"ok": True}))

    def test_not_logged_in_is_reported_before_model_call(self):
        self.login.stop()
        with patch.object(runtime, "auth_status", return_value={"logged_in": False, "auth_method": None}), \
             patch("tools.claude_gateway.infer") as infer:
            code, body = self.call({"tester_id": "123", "model": "test-model", "messages": []})
            self.assertEqual((code, body["login"]), (503, False))
            infer.assert_not_called()
        self.login.start()

    def test_ipv4_also_accepted(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/livez")
            self.assertEqual(connection.getresponse().status, 200)
        except OSError:
            self.skipTest("IPv4 loopback is not mapped to the IPv6 socket on this host")
        finally:
            connection.close()

    def test_environment_config_does_not_invent_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            config = environment_config()
            self.assertEqual(config["token"], "")
            self.assertEqual(config["tester_ids"], [])
            self.assertEqual(config["effort"], "low")
        with patch.dict(os.environ, {"CLAUDE_GATEWAY_TOKEN": "test-only", "CHATGPT_PLAN_TESTER_IDS": "123, 456",
                                    "CLAUDE_GATEWAY_MODEL": "test-model", "PORT": "4567"}, clear=True):
            config = environment_config()
            self.assertEqual(config["tester_ids"], ["123", "456"])
            self.assertEqual(config["port"], 4567)

    def test_hourly_quota(self):
        good = {"tester_id": "123", "model": "test-model", "messages": []}
        with patch("tools.claude_gateway.infer", return_value={"ok": True, "text": "ok"}) as infer:
            for _ in range(60):
                self.assertEqual(self.call(good)[0], 200)
            self.assertEqual(self.call(good)[0], 429)
            self.assertEqual(infer.call_count, 60)

    def test_ambiguous_http_framing_is_rejected(self):
        with patch("tools.claude_gateway.infer") as infer:
            for duplicate in (False, True):
                connection = http.client.HTTPConnection("::1", self.port, timeout=5)
                connection.putrequest("POST", "/infer")
                connection.putheader("Authorization", "Bearer " + self.token)
                connection.putheader("Content-Length", "2")
                connection.putheader("Content-Length" if duplicate else "Transfer-Encoding", "2" if duplicate else "chunked")
                connection.endheaders(b"{}")
                self.assertEqual(connection.getresponse().status, 400)
                connection.close()
            infer.assert_not_called()

    def test_concurrency_and_timeout(self):
        good = {"tester_id": "123", "model": "test-model", "messages": []}
        barrier, release = threading.Barrier(2), threading.Event()
        def blocking(*args):
            barrier.wait(timeout=5)
            release.wait(timeout=5)
            return {"ok": True, "text": "ok"}
        with patch("tools.claude_gateway.infer", side_effect=blocking), ThreadPoolExecutor(1) as pool:
            futures = [pool.submit(self.call, good)]
            try:
                barrier.wait(timeout=5)
                self.assertEqual(self.call(good)[0], 429)
            finally:
                release.set()
            self.assertTrue(all(f.result()[0] == 200 for f in futures))
        with patch("tools.claude_gateway.infer", side_effect=TimeoutError("private")):
            code, body = self.call(good)
            self.assertEqual(code, 504)
            self.assertNotIn("private", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
