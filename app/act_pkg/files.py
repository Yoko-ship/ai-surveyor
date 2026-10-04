"""Файлы загрузки: формат по сигнатуре, размер картинки без раскрытия, проверка содержимого, пережатие для модели."""
import re
import struct
from typing import Optional

import pymupdf

from .. import act_extras as ax
from ..act_texts import t

from .common import AI_MAX_SIDE, _DECODE, FMT_MIME


# --------------------------------------------------------------------------- #
#  Распознавание фото языковой моделью
# --------------------------------------------------------------------------- #

def _format_of(blob: bytes) -> Optional[str]:
    if blob[:4] == b"%PDF":
        return "pdf"
    if blob[:4] == b"PK\x03\x04":
        return ax.zip_format(blob)
    if blob[:3] == b"\xff\xd8\xff":
        return "jpg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


# маркеры JPEG с размерами кадра: SOF0–SOF3, SOF5–SOF7, SOF9–SOF11, SOF13–SOF15 (C4, C8, CC — не кадр)
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _png_size(blob: bytes) -> Optional[tuple]:
    """Ширина и высота из заголовка IHDR (первый блок PNG) — без раскрытия картинки."""
    if len(blob) < 24 or blob[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", blob[16:24])
    return (w, h) if w and h else None


def _jpeg_size(blob: bytes) -> Optional[tuple]:
    """Ширина и высота из маркера SOF: идём по сегментам, данные кадра не читаем."""
    i, n = 2, len(blob)
    while i + 4 <= n:
        if blob[i] != 0xFF:
            return None
        m = blob[i + 1]
        if m == 0xFF:                       # заполнитель перед маркером
            i += 1
            continue
        if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7:
            i += 2                          # маркеры без длины
            continue
        if m in (0xD9, 0xDA):               # конец или начало данных, а кадра так и не было
            return None
        seg = struct.unpack(">H", blob[i + 2:i + 4])[0]
        if seg < 2:
            return None
        if m in _JPEG_SOF:
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", blob[i + 5:i + 9])
            return (w, h) if w and h else None
        i += 2 + seg
    return None


def image_size(blob: bytes, fmt: str) -> Optional[tuple]:
    """(ширина, высота) картинки по заголовку, без раскрытия; не разобрать — None."""
    try:
        if fmt == "png":
            return _png_size(blob)
        if fmt == "jpg":
            return _jpeg_size(blob)
    except struct.error:
        return None
    return None


def check_content(blob: bytes, fmt: str, limits: dict, lang: str) -> Optional[str]:
    """
    Проверка до любого раскрытия: картинка — размеры из заголовка (не прочитали — отказ, больше предела
    мегапикселей — отказ); PDF — число страниц (на сервере PDF не растеризуется). None — файл годится.
    """
    if fmt in ("png", "jpg"):
        size = image_size(blob, fmt)
        if not size:
            return t("ph_dims_unknown", lang)
        mp = float(limits["max_image_mp"])
        if size[0] * size[1] > mp * 1_000_000:
            return t("ph_too_many_px", lang, w=size[0], h=size[1], mp=int(mp) if mp == int(mp) else mp)
        return None
    if fmt == "pdf":
        text_max = int(limits.get("pdf_text_max_pages") or limits["pdf_max_pages"])
        try:
            doc = pymupdf.open(stream=blob, filetype="pdf")
            try:
                if doc.needs_pass:
                    return t("ph_pdf_bad", lang)
                pages = doc.page_count
                # длинный PDF принимается, только если у него есть текстовый слой (договор): разбор без модели
                has_text = pages > int(limits["pdf_max_pages"]) and pages <= text_max and _pdf_has_text(doc)
            finally:
                doc.close()
        except Exception:
            return t("ph_pdf_bad", lang)
        if pages < 1:
            return t("ph_pdf_bad", lang)
        if pages > int(limits["pdf_max_pages"]) and not has_text:
            return t("ph_pdf_pages", lang, n=int(limits["pdf_max_pages"]) if pages <= text_max else text_max)
        return None
    return t("ph_format", lang)


def _pdf_has_text(doc, pages: int = 3) -> bool:
    """Есть ли текстовый слой на первых страницах (сканы в модель берутся не длиннее limits.pdf_max_pages)."""
    got = 0
    for k in range(min(pages, doc.page_count)):
        got += len(re.sub(r"\s+", "", doc[k].get_text("text") or ""))
        if got >= 20:
            return True
    return False


def _for_model(blob: bytes, fmt: str, side: int = AI_MAX_SIDE, quality: int = 85) -> tuple:
    """Снимок для модели: длинная сторона до side, JPEG. Не вышло — исходный файл.
    Размеры картинки уже проверены check_content — раскрытие здесь ограничено пределом мегапикселей."""
    if fmt == "pdf":
        return blob, FMT_MIME["pdf"]
    try:
        with _DECODE:
            pix = pymupdf.Pixmap(blob)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if pix.colorspace is None or pix.colorspace.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            while max(pix.width, pix.height) > side:
                pix.shrink(1)
            out = pix.tobytes("jpg", jpg_quality=quality)
            pix = None
        if out and (len(out) < len(blob) or fmt == "png"):
            return out, "image/jpeg"
    except Exception as e:                   # битая картинка — модель скажет сама, сервер не падает
        print("акт: снимок не пережат:", type(e).__name__)
    return blob, FMT_MIME[fmt]
