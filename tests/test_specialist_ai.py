"""Приоритет lex.uz, ИИ во вкладке специалиста и изоляция кэша. Только временная БД/моки."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent)]
os.environ["SURVEYOR_NO_BACKGROUND"] = "1"
os.environ["LEX_LIVE"] = "0"
from tmpdb import temp_db
from app import legal, legal_live, llm
from app.chatgpt_plan import actor

answers = importlib.import_module("app.legal.answer")
ai = importlib.import_module("app.legal.ai")
market = importlib.import_module("app.legal.market")
Q = "Что будет если страховая сумма больше страховой стоимости?"
OFF = {"status": "off", "source": "lex.uz"}
FAQ = {"text": "Локальная справка", "citations": [], "note": None}


class SpecialistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = temp_db()
        cls.database.__enter__()
        legal.ensure_index()

    @classmethod
    def tearDownClass(cls):
        cls.database.__exit__(None, None, None)

    def setUp(self):
        legal._cache.clear()
        self.actor_mark = actor.set("")

    def tearDown(self):
        actor.reset(self.actor_mark)
        legal._cache.clear()

    def test_lex_wins_over_faq_and_local_index(self):
        passages = legal.search(Q, "ru")
        self.assertTrue(passages)
        live = {"status": "found_base", "source": "lex.uz", "passages": passages}
        with patch.object(answers, "_live", return_value=live) as lookup, \
             patch.object(answers, "faq_match", side_effect=AssertionError("FAQ before lex.uz")), \
             patch.object(answers, "_find_passages", side_effect=AssertionError("local index before lex.uz")), \
             patch.object(llm, "enabled", return_value=True), \
             patch.object(llm, "chat", return_value="Объяснение по найденной норме.") as chat:
            result = answers.ask(Q, "ru", with_ai=True, who="test-owner")
        lookup.assert_called_once_with(Q, "ru", "test-owner")
        self.assertEqual(result["answer"]["found_on"], "lex.uz")
        self.assertTrue(result["citations"])
        self.assertEqual(result["ai"]["status"], "ok")
        self.assertIn("lex.uz", chat.call_args.args[2])
        self.assertGreaterEqual(chat.call_args.kwargs["timeout"], 30)
        self.assertTrue(chat.call_args.kwargs["web_search"])

    def test_faq_still_gets_ai_and_failure_is_not_cached(self):
        unavailable = {"status": "unavailable", "source": "lex.uz"}
        with patch.object(answers, "_live", return_value=unavailable) as lookup, \
             patch.object(answers, "faq_match", return_value=({"id": "fixture"}, .9)), \
             patch.object(answers, "faq_answer", return_value=FAQ), \
             patch.object(llm, "enabled", return_value=True), \
             patch.object(llm, "chat", return_value="Объяснение локальной справки.") as chat:
            for _ in range(2):
                result = answers._ask(Q, "ru", with_ai=True)
                self.assertEqual(result["live"]["status"], "unavailable")
                self.assertEqual(result["ai"]["status"], "ok")
                self.assertFalse(result["cached"])
            self.assertEqual(lookup.call_count, 2)
            self.assertIn("актуальность нормы не подтверждена", chat.call_args.args[2])

    def test_cached_ai_cannot_cross_access_state_or_actor(self):
        with patch.object(answers, "_live", return_value=OFF), \
             patch.object(answers, "faq_match", return_value=({"id": "fixture"}, .9)), \
             patch.object(answers, "faq_answer", return_value=FAQ), \
             patch.object(llm, "enabled", return_value=True) as enabled, \
             patch.object(llm, "chat", return_value="Private test explanation") as chat:
            actor.set("test-authorized-a")
            first = answers._ask(Q, "ru", with_ai=True, who="same-browser")
            self.assertEqual(first["ai"]["status"], "ok")
            self.assertTrue(answers._ask(Q, "ru", with_ai=True, who="same-browser")["cached"])
            enabled.return_value = False  # доступ отозван даже у того же пользователя
            denied = answers._ask(Q, "ru", with_ai=True, who="same-browser")
            self.assertEqual(denied["ai"]["status"], "off")
            self.assertIsNone(denied["ai"]["text"])
            actor.set("test-authorized-b")
            enabled.return_value = True
            other = answers._ask(Q, "ru", with_ai=True, who="same-browser")
            self.assertFalse(other["cached"])
            self.assertEqual(chat.call_count, 2)

    def test_ai_opt_out_and_anonymous_use_no_model(self):
        with patch.object(answers, "_live", return_value=OFF), \
             patch.object(answers, "faq_match", return_value=({"id": "fixture"}, .9)), \
             patch.object(answers, "faq_answer", return_value=FAQ), \
             patch.object(llm, "enabled", return_value=False), patch.object(llm, "chat") as chat:
            self.assertEqual(answers._ask(Q, "ru", with_ai=True)["ai"]["reason_code"], "ai_unavailable")
            self.assertEqual(answers._ask(Q, "ru", with_ai=False)["ai"]["status"], "off")
            chat.assert_not_called()

    def test_law_priority_over_market_and_competitor_names_in_three_languages(self):
        for question in ("Минимальный капитал APEX по закону", "Какие законы регулируют рынок страхования?",
                         "Какая статья регулирует тарифы конкурентов?", "Qonun bo‘yicha ustav kapitali qancha?",
                         "What is the statutory minimum capital for an insurer?", "What does lex.uz say about insurance?"):
            with self.subTest(question=question):
                intent = market.detect(question)
                self.assertFalse(any(intent.get(k) for k in ("is_market", "competitor", "clarify")))
        self.assertTrue(market.detect("Кто лидер рынка по премиям?")["is_market"])

    def test_lex_checked_before_known_silence(self):
        calls = []
        with patch.object(answers, "_live", side_effect=lambda *args: calls.append("lex") or OFF), \
             patch.object(answers, "faq_match", return_value=(None, 0)), \
             patch.object(answers, "silence_match", side_effect=lambda *args: calls.append("silence") or {"id": "fixture"}), \
             patch.object(answers, "silence_answer", return_value=("В локальной справке норма не найдена", [])), \
             patch.object(llm, "enabled", return_value=True), patch.object(llm, "chat", return_value="Проверьте с юристом."):
            result = answers._ask(Q, "ru", with_ai=True)
        self.assertEqual(calls, ["lex", "silence"])
        self.assertEqual(result["ai"]["status"], "ok")

    def test_no_norm_does_not_search_twice_or_invent_citations(self):
        with patch.object(answers, "_live", return_value={"status": "not_found"}) as lookup, \
             patch.object(answers, "faq_match", return_value=(None, 0)), \
             patch.object(answers, "silence_match", return_value=None), \
             patch.object(answers, "_find_passages", return_value=([], [], None)), \
             patch.object(llm, "enabled", return_value=True), patch.object(llm, "chat", return_value="Нормой не подтверждено.") as chat:
            result = answers._ask(Q, "ru", with_ai=True)
        lookup.assert_called_once()
        self.assertEqual(result["citations"], [])
        self.assertIn("НЕ ссылайся на конкретные статьи", chat.call_args.args[1])

    def test_competitor_reference_also_gets_ai(self):
        source = {"answer": {"text": "В документах указано условие."}, "citations": []}
        with patch.object(market, "detect", return_value={"competitor": True}), \
             patch.object(answers, "_competitor_answer", return_value=source), \
             patch.object(llm, "enabled", return_value=True), patch.object(llm, "chat", return_value="Объяснение условия."):
            result = answers.ask("Условия другого страховщика", "ru", with_ai=True)
        self.assertEqual(result["ai"]["status"], "ok")

    def test_search_preserves_legal_act_title(self):
        plan = legal_live.plan_queries("Лицензирование страховой деятельности", "ru")
        self.assertIn("страховой деятельности", plan["queries"])
        self.assertNotIn("страхование деятельности", plan["queries"])

    def test_frontend_requests_ai_with_session_and_language(self):
        source = Path("frontend/tg/js/legal.js").read_text()
        function = source[source.index("async function lgAsk("):source.index("function lgReset()")]
        script = """
const assert = require('node:assert/strict');
const LG = {busy:false, sid:'test-session', items:[]}, LG_MAX = 8, I18N_LANG = 'ru';
const field = {value:'Q'}, $ = () => field;
const lgPaintMsg=()=>{}, lgPaintFeed=()=>{}, lgPaintChips=()=>{}, syncTgButtons=()=>{}, tgBusy=()=>{}, lgTakeRole=()=>{};
const lgStr = String, jsonOpts = (method,body) => ({method, body:JSON.stringify(body)});
let sent;
const api = async (url,opts) => { sent={url,...JSON.parse(opts.body)}; return {ok:true,data:{}}; };
""" + function + """
(async()=>{await lgAsk('Question', 'uz'); assert.deepEqual(sent,
 {url:'/legal/ask', q:'Question', lang:'uz', ai:true, session_id:'test-session'});
 assert.equal(LG.busy,false); assert.equal(LG.items[0].pending,undefined);})().catch(e=>{console.error(e);process.exit(1)});
"""
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
