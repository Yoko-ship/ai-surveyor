"""Изолированные офлайн-проверки шлюза; subprocess Codex и внешний HTTPS замоканы."""
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import codex_gateway as gateway, chatgpt_plan as plan, codex_runtime as runtime, llm
from tools.codex_gateway import handler, environment_config


class GatewayClientTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CODEX_GATEWAY_ENABLED": "1", "CHATGPT_PLAN_ENABLED": "0",
            "CODEX_GATEWAY_URL": "https://gateway.example.test", "CODEX_GATEWAY_TOKEN": "test-only-" * 8,
            "CODEX_GATEWAY_MODEL": "test-model", "CHATGPT_PLAN_TESTER_IDS": "123"})
        self.env.start()
        self.mark = plan.actor.set("123")

    def tearDown(self):
        plan.actor.reset(self.mark)
        self.env.stop()

    def test_allowlist_and_disabled_default(self):
        self.assertTrue(gateway.ready())
        with patch.dict(os.environ, {"CHATGPT_PLAN_TESTER_IDS": ""}):
            self.assertFalse(gateway.ready())
            with patch.object(gateway.urllib.request, "build_opener") as network:
                with self.assertRaises(plan.PlanError):
                    gateway.request([], [])
                network.assert_not_called()
        with patch.dict(os.environ, {"CODEX_GATEWAY_ENABLED": "0"}), patch.object(llm, "get", return_value="gemini"):
            self.assertEqual(llm.provider(), "gemini")

    def test_endpoint_rejects_insecure_or_credential_urls(self):
        for url in ("http://gateway.test", "https://secret@gateway.test", "https://gateway.test/a",
                    "https://gateway.test?token=x", ""):
            with patch.dict(os.environ, {"CODEX_GATEWAY_URL": url}):
                self.assertFalse(gateway.ready())

    def test_gateway_uses_verified_telegram_context_when_direct_plan_is_off(self):
        observed = []
        async def app(scope, receive, send):
            observed.append(plan.actor.get())
        middleware = plan.TesterContextMiddleware(app)
        scope = {"type": "http", "headers": [(b"x-telegram-init-data", b"test") ]}
        checked = {"ok": True, "user": {"id": 123}, "data": {"auth_date": int(time.time())}}
        with patch("app.telegram.bot_token", return_value="test-only"), \
             patch("app.telegram.check_init_data", return_value=checked):
            asyncio.run(middleware(scope, None, None))
        with patch("app.telegram.bot_token", return_value="test-only"), \
             patch("app.telegram.check_init_data", return_value={**checked, "ok": False}):
            asyncio.run(middleware(scope, None, None))
        self.assertEqual(observed, ["123", ""])

    def test_integration_redacts_text_and_status_hides_connection(self):
        with patch.object(llm, "get", return_value="test"), patch.object(llm, "_log_call"), \
             patch.object(gateway.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps({"ok": True, "text": "answer"}).encode())
            result = llm.chat_raw("test", [{"role": "user", "content": "mail@example.com"}])
            self.assertTrue(result["ok"])
            req = opener.return_value.open.call_args.args[0]
            payload = json.loads(req.data)
            self.assertEqual(payload["tester_id"], "123")
            self.assertNotIn("mail@example.com", req.data.decode())
            self.assertEqual(req.full_url, "https://gateway.example.test/infer")
            status = llm.status()
            self.assertEqual(status["provider"], "codex_gateway")
            self.assertEqual(status["base_url"], "")
            self.assertEqual(status["key_mask"], "")

    def test_failure_no_retry_no_paid_fallback(self):
        with patch.object(gateway, "request", side_effect=plan.PlanError("offline")) as call, \
             patch.object(llm, "_log_call"), patch.object(llm, "_request") as paid:
            self.assertFalse(llm.chat_raw("test", [], retries=4)["ok"])
            self.assertEqual(call.call_count, 1)
            paid.assert_not_called()

    def test_http_error_body_not_exposed(self):
        with patch.object(gateway.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError("https://x", 502, "private", {}, None)
            with self.assertRaises(plan.PlanError) as caught:
                gateway.request([], [])
            self.assertNotIn("private", str(caught.exception))
        self.assertIsNone(gateway.NoRedirect().redirect_request(None, None, 302, None, {}, "https://evil.test"))


class RuntimeTests(unittest.TestCase):
    def test_only_final_completed_answer(self):
        def events(*items):
            return "\n".join(json.dumps(x) for x in items)
        message = {"type": "item.completed", "item": {"type": "agent_message", "text": "yes"}}
        done = {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 1}}
        self.assertEqual(runtime.parse_events(events(message, done))["text"], "yes")
        for content in (events(message), events(done), events(message, {"type": "turn.failed"}),
                        events({"type": "item.started", "item": {"type": "command_execution"}}, message, done)):
            with self.assertRaises(runtime.RuntimeFailure):
                runtime.parse_events(content)

    def test_command_isolated_from_personal_config_and_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cmd = runtime.command("/test/codex", "test-model", folder, [])
            for required in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--no-daemon", "--strict-config"):
                self.assertIn(required, cmd)
            flags = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-c"]
            self.assertIn('approval_policy="never"', flags)
            self.assertIn('permissions.gateway.network.enabled=false', flags)
            self.assertIn('features.shell_tool=false', flags)
            self.assertIn('features.apps=false', flags)
            self.assertIn('features.plugins=false', flags)
            self.assertIn('features.view_image=false', flags)
            self.assertTrue(any('"/"="deny"' in v for v in flags))

    def test_pdf_and_image_conversion_and_reject_truncation(self):
        import pymupdf as fitz
        def part(mime, data):
            return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}
        with tempfile.TemporaryDirectory() as tmp, fitz.open() as doc:
            doc.new_page().insert_text((30, 30), "TEST")
            png = doc[0].get_pixmap().tobytes("png")
            result = runtime.attachments([part("image/png", png), part("application/pdf", doc.tobytes())], Path(tmp))
            self.assertEqual(len(result), 2)
            self.assertTrue(all(p.is_file() for p in result))
            for _ in range(12):
                doc.new_page()
            with self.assertRaises(ValueError):
                runtime.attachments([part("application/pdf", doc.tobytes())], Path(tmp))
            with self.assertRaises(ValueError):
                runtime.attachments([part("text/html", b"test")], Path(tmp))


class HttpGatewayTests(unittest.TestCase):
    def setUp(self):
        self.token = "test-only-token-" * 5
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler({"token": self.token,
            "tester_ids": ["123"], "model": "test-model", "codex_binary": "/test/codex"}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
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
        with patch("tools.codex_gateway.infer", return_value={"ok": True, "text": "ok"}) as infer:
            good = {"tester_id": "123", "model": "test-model", "messages": []}
            self.assertEqual(self.call(good, token=False)[0], 401)
            self.assertEqual(self.call({**good, "tester_id": "456"})[0], 403)
            self.assertEqual(self.call({**good, "model": "other"})[0], 400)
            infer.assert_not_called()
            self.assertEqual(self.call(good)[0], 200)
            self.assertEqual(infer.call_count, 1)
            self.assertEqual(self.call(path="/health")[0], 200)
            self.assertEqual(self.call(path="/health", token=False)[0], 401)
            self.assertEqual(self.call(path="/livez", token=False), (200, {"ok": True}))

    def test_environment_config_does_not_invent_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            config = environment_config()
            self.assertEqual(config["token"], "")
            self.assertEqual(config["tester_ids"], [])
        with patch.dict(os.environ, {"CODEX_GATEWAY_TOKEN": "test-only", "CHATGPT_PLAN_TESTER_IDS": "123, 456",
                                    "CODEX_GATEWAY_MODEL": "test-model", "PORT": "4567"}, clear=True):
            config = environment_config()
            self.assertEqual(config["tester_ids"], ["123", "456"])
            self.assertEqual(config["port"], 4567)

    def test_hourly_quota(self):
        good = {"tester_id": "123", "model": "test-model", "messages": []}
        with patch("tools.codex_gateway.infer", return_value={"ok": True, "text": "ok"}) as infer:
            for _ in range(60):
                self.assertEqual(self.call(good)[0], 200)
            self.assertEqual(self.call(good)[0], 429)
            self.assertEqual(infer.call_count, 60)

    def test_concurrency_and_timeout(self):
        good = {"tester_id": "123", "model": "test-model", "messages": []}
        barrier, release = threading.Barrier(2), threading.Event()
        def blocking(*args):
            barrier.wait(timeout=5)
            release.wait(timeout=5)
            return {"ok": True, "text": "ok"}
        with patch("tools.codex_gateway.infer", side_effect=blocking), ThreadPoolExecutor(1) as pool:
            futures = [pool.submit(self.call, good)]
            try:
                barrier.wait(timeout=5)
                self.assertEqual(self.call(good)[0], 429)
            finally:
                release.set()
            self.assertTrue(all(f.result()[0] == 200 for f in futures))
        with patch("tools.codex_gateway.infer", side_effect=TimeoutError("private")):
            code, body = self.call(good)
            self.assertEqual(code, 504)
            self.assertNotIn("private", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
