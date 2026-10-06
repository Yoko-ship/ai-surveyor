"""Офлайн-контракты subscription-адаптера: доступ, токены, stream, вложения.

Только временное хранилище и сетевые заглушки; настоящая подписка не используется.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import chatgpt_plan as plan, llm


def credentials(**changes):
    return {"client_id": "oaiapp_test", "access_token": "test-access", "refresh_token": "test-refresh",
            "token_type": "Bearer", "expires_at": time.time() + 3600,
            "ext_agent_host_id": "urn:uuid:00000000-0000-4000-8000-000000000001",
            "scopes": sorted(plan.REQUIRED_SCOPES), **changes}


def completion(text="Тестовый ответ"):
    return {"type": "response.completed", "response": {"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}}}


def stream(*events):
    return io.BytesIO(b"".join(b"event: test\ndata: " + json.dumps(e).encode() + b"\n\n" for e in events))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STORAGE_DIR": self.folder.name, "CHATGPT_PLAN_ENABLED": "1",
            "CHATGPT_PLAN_TESTER_IDS": "123", "CHATGPT_PLAN_MODEL": "test-model",
            "CHATGPT_PLAN_AUTH_JSON": json.dumps(credentials())})
        self.env.start()
        self.mark = plan.actor.set("123")
        plan._hits.clear()

    def tearDown(self):
        plan.actor.reset(self.mark)
        self.env.stop()
        self.folder.cleanup()

    def test_disabled_keeps_existing_provider(self):
        with patch.dict(os.environ, {"CHATGPT_PLAN_ENABLED": "0"}), patch.object(llm, "get", return_value="gemini"):
            self.assertEqual(llm.provider(), "gemini")
            self.assertFalse(plan.ready())
        with patch.dict(os.environ, {"CHATGPT_PLAN_ENABLED": "0"}), patch.object(llm, "get", return_value="chatgpt_plan"):
            self.assertEqual(llm.provider(), "none")
        self.assertFalse(plan.auth_path().exists())

    def test_default_deny_and_no_paid_fallback(self):
        with patch.dict(os.environ, {"CHATGPT_PLAN_TESTER_IDS": ""}), patch.object(plan.urllib.request, "urlopen") as network:
            self.assertFalse(plan.ready())
            with self.assertRaises(plan.PlanError):
                plan.request([], [])
            network.assert_not_called()
        mark = plan.actor.set("456")
        try:
            with patch.object(llm, "_log_call"), patch.object(llm, "_request") as paid:
                result = llm.chat_raw("test", [{"role": "user", "content": "test"}])
                self.assertFalse(result["ok"])
                paid.assert_not_called()
        finally:
            plan.actor.reset(mark)

    def test_readiness_does_not_create_files_or_reveal_tokens(self):
        self.assertTrue(plan.ready())
        self.assertFalse(plan.auth_path().exists())
        with patch.object(llm, "get", return_value="test"):
            status = llm.status()
        self.assertEqual(status["key_mask"], "")
        self.assertNotIn("test-access", json.dumps(status))
        self.assertNotIn("test-refresh", json.dumps(status))
        with patch.dict(os.environ, {"CHATGPT_PLAN_MODEL": ""}):
            self.assertFalse(plan.ready())

    def test_scopes_client_and_expiry_required(self):
        for changes in [{"scopes": []}, {"client_id": "dynamic_agent_client"}, {"expires_at": None},
                        {"expires_at": float("nan")}, {"expires_at": float("inf")},
                        {"refresh_token": ""}, {"token_type": "other"}]:
            with self.subTest(changes=changes), patch.dict(os.environ, {"CHATGPT_PLAN_AUTH_JSON": json.dumps(credentials(**changes))}):
                self.assertFalse(plan.ready())

    def test_atomic_storage_permissions_and_disk_precedence(self):
        plan._save(credentials(access_token="new-on-disk"))
        self.assertEqual(plan.auth_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(plan._read()["access_token"], "new-on-disk")
        self.assertEqual(list(plan.auth_path().parent.iterdir()), [plan.auth_path()])
        plan.auth_path().chmod(0o644)
        self.assertFalse(plan.ready())

    def test_symlink_credentials_rejected(self):
        plan.auth_path().parent.mkdir()
        target = Path(self.folder.name) / "other.json"
        target.write_text(json.dumps(credentials()))
        plan.auth_path().symlink_to(target)
        self.assertFalse(plan.ready())
        with self.assertRaises(plan.PlanError):
            plan._save(credentials())

    def test_refresh_rotates_once_across_threads(self):
        plan._save(credentials(expires_at=time.time() - 1))
        refreshed = {"access_token": "new-access", "refresh_token": "new-refresh",
                     "token_type": "Bearer", "expires_in": 3600}
        with patch.object(plan.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(refreshed).encode())) as network:
            with ThreadPoolExecutor(2) as pool:
                tokens = list(pool.map(lambda _: plan._access_token(), range(2)))
            self.assertEqual(tokens, ["new-access"] * 2)
            self.assertEqual(network.call_count, 1)
            request = network.call_args.args[0]
            fields = parse_qs(request.data.decode())
            self.assertEqual(request.full_url, plan.TOKEN_URL)
            self.assertEqual(fields["client_id"], ["oaiapp_test"])
            self.assertEqual(fields["resource"], [plan.API])
            self.assertNotIn("scope", fields)
        self.assertEqual(plan._read()["refresh_token"], "new-refresh")

    def test_failed_refresh_keeps_previous_pair_and_hides_upstream(self):
        record = credentials(expires_at=1)
        plan._save(record)
        error = plan.urllib.error.HTTPError(plan.TOKEN_URL, 400, "sensitive upstream text", {}, None)
        with patch.object(plan.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(plan.PlanError) as caught:
                plan._access_token()
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual(plan._read(), record)

    def test_complete_stream_only(self):
        result = plan._completed(stream({"type": "response.output_text.delta", "delta": "partial"}, completion()), time.monotonic() + 10)
        self.assertEqual(result["text"], "Тестовый ответ")
        self.assertEqual(result["usage"]["total_tokens"], 28)
        for response in [stream({"type": "response.output_text.delta", "delta": "partial"}),
                         stream({"type": "response.failed"}), stream({"type": "response.incomplete"}),
                         stream(completion("")), io.BytesIO(b"data: invalid\n\n")]:
            with self.assertRaises(plan.PlanError):
                plan._completed(response, time.monotonic() + 10)
        with self.assertRaises(plan.PlanError):
            plan._completed(stream(completion()), time.monotonic() - 1)

    def test_multimodal_body_uses_supported_parameters(self):
        files, notes = llm.prepare_files([
            {"name": "private-name.png", "mime": "image/png", "data": b"fake-test-image"},
            {"name": "private-name.pdf", "mime": "application/pdf", "data": b"%PDF-test"}])
        self.assertEqual(notes, [])
        body = plan.request_body([{"role": "system", "content": "rules"},
            {"role": "assistant", "content": "old answer"}, {"role": "user", "content": "question"}], files)
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertEqual(body["instructions"], "rules")
        self.assertEqual(body["input"][-1]["content"][1]["type"], "input_image")
        self.assertEqual(body["input"][-1]["content"][2]["type"], "input_file")
        self.assertNotIn("private-name", json.dumps(body))
        self.assertFalse(set(body) & {"temperature", "max_output_tokens", "max_tokens", "previous_response_id", "tools"})

    def test_request_endpoint_and_redacted_llm_integration(self):
        with patch.object(plan.urllib.request, "urlopen", return_value=stream(completion())) as network, patch.object(llm, "_log_call"):
            result = llm.chat_raw("test", [{"role": "system", "content": "rules"},
                {"role": "user", "content": "email: tester@example.com"}], retries=3)
            self.assertTrue(result["ok"])
            request = network.call_args.args[0]
            self.assertEqual(request.full_url, plan.API + "/responses")
            self.assertNotIn(b"tester@example.com", request.data)
            self.assertEqual(request.get_header("Authorization"), "Bearer test-access")
            self.assertEqual(network.call_count, 1)
        with patch.object(plan, "request", side_effect=plan.PlanError("safe error")) as call, patch.object(llm, "_log_call"):
            self.assertFalse(llm.chat_raw("test", [{"role": "user", "content": "test"}], retries=3)["ok"])
            self.assertEqual(call.call_count, 1)

    def test_quota_precedes_network(self):
        plan._hits["123"].extend([time.monotonic()] * 60)
        with patch.object(plan.urllib.request, "urlopen") as network, self.assertRaises(plan.PlanError):
            plan.request([], [])
        network.assert_not_called()

    def test_photo_worker_preserves_tester_context(self):
        from app.act_pkg import recognize
        observed = []
        def reply(*args, **kwargs):
            observed.append(plan.actor.get())
            return {"text": "{}"}
        limits = {"ai_calls_per_hour": 60, "ai_deadline_sec": 10, "ai_timeout_sec": 5}
        with patch.object(recognize.AI_CALLS, "take", return_value={"ok": True}), patch.object(llm, "chat_raw", side_effect=reply):
            result = recognize._model_call("test", [], [], limits, time.monotonic(), "ru", json.loads)
            self.assertTrue(result["ok"])
            mark = plan.actor.set("")
            try:
                recognize._model_call("test", [], [], limits, time.monotonic(), "ru", json.loads)
            finally:
                plan.actor.reset(mark)
        self.assertEqual(observed, ["123", ""])

    @unittest.skipUnless(shutil.which("node"), "Node.js required for frontend contract")
    def test_frontend_passes_init_data_only_to_own_origin(self):
        source = (Path(__file__).resolve().parent.parent / "frontend/tg/js/core.js").read_text()
        function = source[source.index("async function api("):source.index("const jsonOpts")]
        script = r'''
const assert = require("node:assert/strict");
let IN_TG = true, TOKEN = null;
const TG = {initData: "signed-test-data"};
const location = {href: "https://example.test/tg", origin: "https://example.test"};
const calls = [];
async function fetch(url, opts) { calls.push(opts.headers || {}); return {ok: true, json: async () => ({})}; }
async function run() {
  await api("/llm/status", {headers: {"Content-Type": "application/json"}});
  await api("https://external.test/example");
  IN_TG = false;
  await api("/llm/status");
  assert.equal(calls[0]["X-Telegram-Init-Data"], TG.initData);
  assert.equal(calls[0]["Content-Type"], "application/json");
  assert.equal(calls[1]["X-Telegram-Init-Data"], undefined);
  assert.equal(calls[2]["X-Telegram-Init-Data"], undefined);
}
run().catch(e => { console.error(e); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-e", function + script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_signed_telegram_access_and_context_isolation(self):
        bot = "test-only-bot-token"
        def signed(uid="123", age=0):
            values = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": int(uid)})}
            secret = hmac.new(b"WebAppData", bot.encode(), hashlib.sha256).digest()
            values["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
            return urlencode(values).encode()
        async def run():
            seen = []
            async def app(scope, receive, send):
                await asyncio.sleep(0)
                seen.append((scope["test"], plan.actor.get()))
            middleware = plan.TesterContextMiddleware(app)
            raw = signed()
            cases = {"valid": raw, "missing": b"", "forged": raw.replace(b"123", b"999"),
                     "other": signed("456"), "old": signed(age=3601), "future": signed(age=-600)}
            with patch("app.telegram.bot_token", return_value=bot):
                await asyncio.gather(*(middleware({"type": "http", "test": label,
                    "headers": [(b"x-telegram-init-data", value)]}, None, None) for label, value in cases.items()))
            self.assertEqual(dict(seen), {k: "123" if k == "valid" else "" for k in cases})
            self.assertEqual(plan.actor.get(), "123")  # внешний контекст восстановлен
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
