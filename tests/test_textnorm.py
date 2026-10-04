"""
Нормализация текста после выноса в app/textnorm.py (04.10.2026): результат прежний.

Эталон — tests/data/textnorm_cases.json: 20 строк (узбекская латиница с разными апострофами, кириллица,
английский, невидимые знаки, пустая строка) и то, что на них возвращали функции ДО переноса:
ingest.detect_language, legal.fold / norm / quote_norm / detect_lang, market_expert.nrm, docparse.norm.

Запуск:
    sandbox\.venv\Scripts\python.exe tests\test_textnorm.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import docparse, ingest, legal, textnorm  # noqa: E402
from app import market_expert as mx  # noqa: E402

CASES = Path(__file__).resolve().parent / "data" / "textnorm_cases.json"
passed, failed = 0, 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, str(extra)[:400])


def main():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    ok("эталон: 20 примеров", len(cases) == 20, len(cases))
    for i, c in enumerate(cases, 1):
        s = c["s"]
        got = {"lang": (ingest.detect_language(s) or {}).get("language"), "fold": legal.fold(s),
               "norm": legal.norm(s), "quote_norm": legal.quote_norm(s), "detect_lang": legal.detect_lang(s),
               "mx_nrm": mx.nrm(s), "doc_norm": docparse.norm(s)}
        diff = {k: (c[k], v) for k, v in got.items() if c[k] != v}
        ok("%2d. %s" % (i, s[:50].replace("\n", " ") or "(пусто)"), not diff, diff)
    print("одна утилита — три прежних набора апострофов")
    ok("legal: ʻ ‘ ’ ` ´ ′ ʽ ' снимаются", textnorm.fold("gʻ g‘ g’ g` g´ g′ gʽ g'") == "g g g g g g g g")
    ok("рынок: и ʹ тоже", textnorm.fold_lower("Oʹzbekinvest") == "ozbekinvest")
    ok("документы: апострофы → «'», ʽ не трогаем", textnorm.doc_norm("Sugʻurta gʽ") == "sug'urta gʽ")
    ok("невидимые знаки и мягкий перенос сняты", textnorm.fold("До\u00adпол\u200bни\ufeffтельная") == "Дополнительная")
    print(f"\nИтого: {passed} ок, {failed} плохо")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
