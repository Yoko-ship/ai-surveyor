"""
Нормализация текста: апострофы узбекской латиницы, невидимые знаки, пробелы.

Одно место вместо трёх копий (app/legal, app/legal/market — бывший market_expert, app/docparse → app/ingest).
Наборы апострофов у читателей исторически разные, и менять их нельзя: на нормализованный текст
опираются индекс специалиста (legal_chunks), словари определения языка (docs/ingest_dicts.json)
и сверка дословности цитат. Поэтому здесь три набора и три готовые свёртки — ровно как было.
"""
import re
import unicodedata
from functools import lru_cache

# legal: снимаются при индексации и в вопросе («sugʻurta» = «sug'urta» = «sugurta»)
APOSTROPHES = "'‘’ʻʼʽ′`´"
# вопросы о рынке: то же плюс ʹ (U+02B9)
APOSTROPHES_MARKET = APOSTROPHES + "ʹ"
# разбор документов (docparse/ingest): сводятся к одному «'»; ʽ (U+02BD) в этом наборе нет
APOSTROPHES_DOC = "‘’ʻʼ`´′ʹ'"

# невидимые знаки выгрузки: BOM, метки направления письма, мягкий перенос (стоит внутри слова)
ZERO_WIDTH = "\ufeff\u200b\u200c\u200d\u200e\u200f\u00ad\u2060"

_WS_RE = re.compile(r"\s+")
_ZW_RE = re.compile("[" + ZERO_WIDTH + "]")


@lru_cache(maxsize=8)
def _apo_re(chars: str):
    return re.compile("[" + re.escape(chars) + "]")


def squash_spaces(s: str) -> str:
    """Любые пробельные знаки подряд → один пробел, края обрезаны."""
    return _WS_RE.sub(" ", s or "").strip()


def drop_zero_width(s: str) -> str:
    return _ZW_RE.sub("", s or "")


def drop_apostrophes(s: str, chars: str = APOSTROPHES) -> str:
    return _apo_re(chars).sub("", s or "")


def unify_apostrophes(s: str, chars: str = APOSTROPHES_DOC, to: str = "'") -> str:
    return _apo_re(chars).sub(to, s or "")


def fold(s: str) -> str:
    """Индекс и цитаты специалиста: NFC, невидимые знаки и апострофы сняты, пробелы схлопнуты.
    Регистр сохраняется (его складывает токенайзер FTS5 и сверка дословности)."""
    s = unicodedata.normalize("NFC", s or "")
    return squash_spaces(drop_apostrophes(drop_zero_width(s)))


def fold_lower(s: str) -> str:
    """Распознавание вопросов о рынке: NFC, нижний регистр, ё→е, апострофы сняты, пробелы схлопнуты."""
    s = unicodedata.normalize("NFC", s or "").lower().replace("ё", "е")
    return squash_spaces(drop_apostrophes(s, APOSTROPHES_MARKET))


def doc_norm(text: str) -> str:
    """Разбор документов: нижний регистр, ё→е, апострофы → «'», пробелы схлопнуты.
    Кириллица и апостроф сохраняются — на них опираются словари языка (ingest_dicts.json)."""
    t = (text or "").lower().replace("ё", "е")
    t = unify_apostrophes(t).replace("\u00a0", " ")
    return squash_spaces(t)
