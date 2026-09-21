"""
Словарь интерфейса: русский, узбекский (латиница), английский.

Что здесь есть:
  * загрузка словарей app/i18n/{ru,uz,en}.json с кэшем в памяти;
  * t(key, lang, **kwargs) — перевод с подстановкой; нет ключа — отдаём русский текст,
    а пропуск запоминаем в счётчике misses() (сломать страницу перевод не может);
  * pick_lang(...) — выбор языка: ?lang= → cookie lang → language_code из Telegram → ru;
  * rule_text(rule_code, lang, ...) — заголовок и пояснение проверки движка по коду правила
    (app/engine.py, поле "rule"); движок этот модуль не знает и ничего о нём не решает;
  * router: GET /i18n/langs и GET /i18n/{lang}.json.

Подключение к серверу — одна строка в списке модулей в конце app/main.py:
    ("i18n", "i18n_router")

Доступ: /i18n/* закрыт guard'ом, как и всё остальное. Если словарь понадобится на экране
логина (до входа) — в app/guard.py в WHITE_PREFIX нужно добавить "/i18n/". Сам guard не трогаем.

Словари собраны из docs/i18n_terms.json (термины от юриста, со ссылкой на норму) и подписей
экранов. В uz.json в блоке "_meta" лежит список "draft" — ключи, где узбекская формулировка
рабочая, а не взята из нормы: их проверяет юрист.
"""
import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

DIR = Path(__file__).resolve().parent / "i18n"

LANGS = ("ru", "uz", "en")
DEFAULT_LANG = "ru"
COOKIE = "lang"                 # имя cookie, в которой страница запоминает выбор
CACHE_SECONDS = 300             # словарь меняется редко, но и залёживаться не должен

_cache: dict = {}
_lock = threading.Lock()
_misses: dict = {}              # ключ -> сколько раз не нашёлся в выбранном языке


# --------------------------------------------------------------------------- #
#  загрузка
# --------------------------------------------------------------------------- #

def normalize_lang(code: Optional[str]) -> Optional[str]:
    """'uz-Latn', 'UZ_latn', 'ru-RU', 'en-US' → 'uz' / 'ru' / 'en'. Прочее → None."""
    if not code:
        return None
    base = str(code).strip().lower().replace("_", "-").split("-")[0]
    return base if base in LANGS else None


def load(lang: str) -> dict:
    """Словарь языка целиком, вместе с блоком _meta. Читается один раз на процесс."""
    lang = normalize_lang(lang) or ""
    if lang not in LANGS:
        raise KeyError(lang)
    got = _cache.get(lang)
    if got is None:
        with _lock:
            got = _cache.get(lang)
            if got is None:
                got = json.loads((DIR / f"{lang}.json").read_text(encoding="utf-8"))
                _cache[lang] = got
    return got


def reload_all() -> None:
    """Сбросить кэш — после правки json-файлов без перезапуска сервера."""
    with _lock:
        _cache.clear()


def keys(lang: str = DEFAULT_LANG) -> list:
    """Все ключи словаря без служебных (_meta)."""
    return sorted(k for k in load(lang) if not k.startswith("_"))


def misses() -> dict:
    """Диагностика: какие ключи не нашлись и сколько раз. Ключ вида 'uz:calc.rate'."""
    return dict(_misses)


def reset_misses() -> None:
    _misses.clear()


# --------------------------------------------------------------------------- #
#  перевод
# --------------------------------------------------------------------------- #

def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """
    Перевод ключа. Порядок: выбранный язык → русский → сам ключ.
    Пропуск не роняет страницу, а попадает в misses().
    Подстановка — именованная: t("calc.premium_note", "uz", days=90).
    """
    lang = normalize_lang(lang) or DEFAULT_LANG
    text = None
    try:
        text = load(lang).get(key)
    except (KeyError, OSError, ValueError):
        text = None
    if text is None:
        _misses[f"{lang}:{key}"] = _misses.get(f"{lang}:{key}", 0) + 1
        try:
            text = load(DEFAULT_LANG).get(key)
        except (KeyError, OSError, ValueError):
            text = None
    if text is None:
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # в тексте остался знак { } или не хватает параметра — отдаём как есть,
            # чтобы человек увидел текст, а не ошибку сервера
            return text
    return text


def pick_lang(query_lang: Optional[str] = None,
              cookie: Optional[str] = None,
              tg_language_code: Optional[str] = None) -> str:
    """
    Какой язык показывать. Приоритет:
      1. параметр ?lang= — человек нажал переключатель;
      2. cookie lang — прошлый выбор;
      3. language_code из Telegram initData (uz, uz-Latn, ru, en; прочее → ru);
      4. русский.
    """
    for src in (query_lang, cookie, tg_language_code):
        got = normalize_lang(src)
        if got:
            return got
    return DEFAULT_LANG


# --------------------------------------------------------------------------- #
#  проверки и рекомендации движка (app/engine.py)
# --------------------------------------------------------------------------- #
# rule-код → варианты сообщения. Ключ i18n: check.<rule>.<вариант>.title / .detail
# У части правил один код даёт разные тексты (сумма выше/ниже/равна оценке), поэтому
# вариант выбирается по статусу проверки, а при совпадении статусов — по заголовку.
RULE_KEYS = {
    "manual_rate":       {"warn": "manual_rate.warn", "stop": "manual_rate.stop"},
    "sum_over_value":    {"stop": "sum_over_value.stop"},
    "underinsurance":    {"warn": "underinsurance.warn"},
    "sum_ok":            {"ok": "sum_ok.ok"},
    "sum_vs_valuation":  {"ok": "sum_vs_valuation.equal",
                          "warn": "sum_vs_valuation.over", "*": ["sum_vs_valuation.over",
                                                                "sum_vs_valuation.under",
                                                                "sum_vs_valuation.equal"]},
    "value_agreed":      {"warn": "value_agreed.warn"},
    "pricing_mode":      {"ok": "pricing_mode.negotiated", "*": ["pricing_mode.negotiated",
                                                                 "pricing_mode.program",
                                                                 "pricing_mode.treaty"]},
    "statutory_rate":    {"warn": "statutory_rate.warn"},
    "multi_class":       {"ok": "multi_class.main", "warn": "multi_class.mismatch",
                          "*": ["multi_class.main", "multi_class.part", "multi_class.mismatch"]},
    "min_rate":          {"ok": "min_rate.ok", "stop": "min_rate.stop"},
    "below_technical":   {"ok": "below_technical.ok", "warn": "below_technical.warn"},
    "risk_limit_20":     {"ok": "risk_limit_20.ok", "stop": "risk_limit_20.stop",
                          "warn": "risk_limit_20.unknown"},
    "credit_uncovered":  {"ok": "credit_uncovered.ok", "stop": "credit_uncovered.stop"},
    "credit_payer":      {"stop": "credit_payer.stop"},
    "docs_missing":      {"warn": "docs_missing.warn"},
    "docs_ok":           {"ok": "docs_ok.ok"},
    "no_franchise":      {"warn": "no_franchise.warn"},
    "cat_accumulation":  {"warn": "cat_accumulation.warn"},
    "premium_unpaid":    {"warn": "premium_unpaid.warn"},
    "disclosure":        {"warn": "disclosure.warn"},
}

# Рекомендации «как удешевить»: движок отдаёт готовый русский текст и вид (kind).
REC_TEXT_KEYS = {
    "Франшиза 1% страховой суммы": "rec.franchise_1pct",
    "Договор с охраной": "rec.guard_contract",
    "Спринклерная система": "rec.sprinkler",
    "Исключить землетрясение": "rec.drop_earthquake",
    "Снизить сумму до страховой стоимости": "rec.sum_to_value",
}
REC_KIND_KEYS = {
    "франшиза": "rec.kind.franchise",
    "защита": "rec.kind.protection",
    "риск": "rec.kind.peril",
    "сумма": "rec.kind.sum",
}

# Что движок отдаёт без перевода — переводить нечего, текст лежит в базе:
#   preventive_measures[].measure / .why  — таблица measures (справочник предписаний);
#   explanation[] (цепочка расчёта)        — названия факторов и вариантов из coefficients;
#   названия продуктов и классов           — таблицы products / classes.
UNTRANSLATED_SOURCES = ("measures", "coefficients", "products", "classes")


def rule_codes() -> list:
    """Коды правил, для которых в словаре есть текст."""
    return sorted(RULE_KEYS)


def rule_text(rule_code: str, lang: Optional[str] = None,
              status: Optional[str] = None, variant: Optional[str] = None,
              title_ru: Optional[str] = None, **kwargs) -> dict:
    """
    Заголовок и пояснение проверки по коду правила.

    rule_text("min_rate", "uz", status="ok", product="0807", floor="0,050%", source="регулятор")
    → {"title": "Minimal tarifga rioya qilindi", "detail": "0807 mahsuloti boʻyicha minimum — …"}

    Если код неизвестен — возвращаем {"title": rule_code, "detail": ""}: страница переживёт
    новое правило в движке, а пропуск попадёт в misses().
    """
    base = _rule_key(rule_code, status, variant, title_ru)
    if base is None:
        _misses[f"rule:{rule_code}"] = _misses.get(f"rule:{rule_code}", 0) + 1
        return {"title": rule_code, "detail": ""}
    return {"title": t(f"check.{base}.title", lang, **kwargs),
            "detail": t(f"check.{base}.detail", lang, **kwargs)}


def _rule_key(rule_code, status, variant, title_ru):
    table = RULE_KEYS.get(rule_code)
    if not table:
        return None
    if variant:
        name = f"{rule_code}.{variant}"
        if name in (table.get("*") or list(table.values())):
            return name
        return name            # вариант задал вызывающий — доверяем ему
    if title_ru:               # заголовок пришёл от движка: по нему вариант виден точно
        ru = load(DEFAULT_LANG)
        for cand in (table.get("*") or [v for k, v in table.items() if k != "*"]):
            if ru.get(f"check.{cand}.title") == title_ru:
                return cand
    if status and status in table:
        return table[status]
    opts = table.get("*") or [v for k, v in table.items() if k != "*"]
    return opts[0] if opts else None


def check_text(check: dict, lang: Optional[str] = None, **kwargs) -> dict:
    """Готовая проверка движка (элемент calculate()['checks']) → переведённые заголовок и текст."""
    return rule_text(check.get("rule", ""), lang, status=check.get("status"),
                     title_ru=check.get("title"), **kwargs)


def rec_text(recommendation: dict, lang: Optional[str] = None) -> dict:
    """Рекомендация движка (элемент calculate()['recommendations']) → перевод текста и вида."""
    text = recommendation.get("text") or ""
    kind = recommendation.get("kind") or ""
    tk, kk = REC_TEXT_KEYS.get(text), REC_KIND_KEYS.get(kind)
    if tk is None and text:
        _misses[f"rec:{text}"] = _misses.get(f"rec:{text}", 0) + 1
    return {"text": t(tk, lang) if tk else text,
            "kind": t(kk, lang) if kk else kind}


# --------------------------------------------------------------------------- #
#  маршруты
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/i18n", tags=["i18n"])
i18n_router = router          # имя для списка модулей в app/main.py


@router.get("/langs")
def langs():
    """Какие языки есть и какой показываем, если человек ничего не выбрал."""
    return {"langs": [{"code": c, "name": t("common.lang_" + c, c)} for c in LANGS],
            "default": DEFAULT_LANG, "cookie": COOKIE}


@router.get("/{lang}.json")
def dictionary(lang: str):
    """Словарь целиком — страница забирает его одним запросом при загрузке."""
    if normalize_lang(lang) != lang or lang not in LANGS:
        raise HTTPException(status_code=404,
                            detail=f"Языка «{lang}» нет. Есть: {', '.join(LANGS)}.")
    return JSONResponse(load(lang), headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"})
