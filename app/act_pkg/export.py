"""Выгрузка акта: Word (app/docx_lite.py) и PDF (pymupdf), страница и картинка страхового скоринга."""
from pathlib import Path

import pymupdf

from .. import act_scoring as asc
from ..act_texts import t
from ..docx_lite import TEXT_WIDTH, Docx
from ..proposal import A4_H, A4_W, BLACK, CONTENT_W, GRAY, MARGIN, _Pdf


# --------------------------------------------------------------------------- #
#  Word и PDF
# --------------------------------------------------------------------------- #

def scoring_png(act: dict) -> bytes:
    """Картинка шкалы скоринга с плашкой класса (тот же код рисования, что у страницы PDF)."""
    regular, bold = _fonts()
    fit = lambda s, font: "".join(ch if ord(ch) < 128 or font.has_glyph(ord(ch)) else GLYPH_FALLBACK.get(ch, "?")  # noqa
                                  for ch in str(s))
    return asc.gauge_png(act["scoring"], regular, bold, fit)


def build_docx(act: dict) -> bytes:
    """Акт (ответ render) → DOCX: секция скоринга, шапка, пять разделов со строками, списками и таблицами."""
    lang = act["lang"]
    doc = Docx(lang={"ru": "ru-RU", "uz": "uz-Latn-UZ", "en": "en-GB"}[lang])
    if (act.get("scoring") or {}).get("available"):
        # первая секция — страховой скоринг объекта (01.10.2026), шкала — картинкой PNG; затем прежний акт
        asc.docx_section(doc, act["scoring"], scoring_png(act))
    if act.get("insurer_known"):
        doc.para(act["insurer"], bold=True, color="555555", size=20, align="center", after=60)
    doc.title(act["title"])
    doc.table([[r["label"], str(r["value"])] for r in act["header"]], widths=[3200, TEXT_WIDTH - 3200],
              header=False)
    for s in act["sections"]:
        doc.heading(f"{s['n']}. {s['title']}")
        for p in s["paragraphs"]:
            doc.para(p)
        if s["rows"]:
            doc.table([[r["label"], str(r["value"]), r.get("note") or ""] for r in s["rows"]],
                      widths=[2900, 3900, TEXT_WIDTH - 6800], header=False)
        for p in s.get("source_lines") or []:
            doc.para(p, italic=True, color="555555", size=18, after=60)
        for li in s.get("lists") or []:
            doc.para(li["title"], bold=True, after=60)
            tb = li.get("table")
            if tb and tb.get("rows"):
                # аналитика раздела 4: таблица, под ней пояснения и строки «Источник: …»
                w = tb.get("widths") or [100 // len(tb["columns"])] * len(tb["columns"])
                widths = [int(TEXT_WIDTH * x / sum(w)) for x in w]
                doc.table([list(tb["columns"])] + [[str(c) for c in r] for r in tb["rows"]], widths=widths,
                          header=True, size=17)
                for p in li.get("notes") or []:
                    doc.para(p, size=18, after=60)
                for p in li.get("sources") or []:
                    doc.para(p, italic=True, color="555555", size=16, after=40)
                continue
            for it in li["items"]:
                doc.bullet(it)
    doc.rule()
    doc.para(act["footer"], bold=True, italic=True)
    return doc.to_bytes()


# Windows — Arial; сервер (Dockerfile ставит fonts-dejavu-core) — DejaVu Sans, в нём есть узбекская ʻ
FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)
GLYPH_FALLBACK = {"ʻ": "'", "ʼ": "'", "−": "-", "—": "-", "×": "x", "≤": "<=", "≥": ">=", "→": "->",
                  "«": '"', "»": '"', "…": "...", "\u00a0": " "}


def _fonts():
    for reg, bold in FONT_CANDIDATES:
        if Path(reg).exists() and Path(bold).exists():
            return pymupdf.Font(fontfile=reg), pymupdf.Font(fontfile=bold)
    # на сервере без шрифтов — встроенные Helvetica (кириллица в них есть, узбекской ʻ нет)
    return pymupdf.Font("helv"), pymupdf.Font("hebo")


def build_pdf(act: dict, scoring_only: bool = False) -> bytes:
    """Акт в PDF: первая страница — страховой скоринг объекта (01.10.2026), затем прежний акт из пяти разделов.
    scoring_only — только страница скоринга (GET /act/{id}/scoring.pdf)."""

    class ActPdf(_Pdf):
        def __init__(self):
            self.doc = pymupdf.open()
            self.regular, self.bold = _fonts()
            self.page = None
            self.writers = {}
            self.y = 0.0
            self.footer = act["footer"]
            self.new_page()

        def _fit(self, s, font):
            return "".join(ch if ord(ch) < 128 or font.has_glyph(ord(ch)) else GLYPH_FALLBACK.get(ch, "?")
                           for ch in str(s))

        def _put(self, x, baseline, s, font, size, color):
            super()._put(x, baseline, self._fit(s, font), font, size, color)

        def heading(self, s, size=12.5):
            self.ensure(90)                  # заголовок раздела не остаётся внизу страницы один
            super().heading(s, size)

        def bullet(self, s, size=10, color=BLACK):
            self.ensure(size * 1.35 * 2)     # маркер не отрывается от своего текста
            super().bullet(s, size=size, color=color)

        def finish(self) -> bytes:
            self._flush()
            n = self.doc.page_count
            for i, page in enumerate(self.doc, start=1):
                tw = pymupdf.TextWriter(page.rect)
                s = self._fit(t("page", act["lang"], i=i, n=n), self.regular)
                w = self.regular.text_length(s, fontsize=8)
                tw.append((A4_W - MARGIN - w, A4_H - MARGIN + 6), s, font=self.regular, fontsize=8)
                foot = self._fit(self.footer, self.regular)
                tw.append((MARGIN, A4_H - MARGIN + 6), foot[:95], font=self.regular, fontsize=8)
                tw.write_text(page, color=GRAY)
            self.doc.subset_fonts()
            return self.doc.tobytes(garbage=3, deflate=True)

    pdf = ActPdf()
    if (act.get("scoring") or {}).get("available"):
        asc.draw_page(pdf.page, act["scoring"], pdf.regular, pdf.bold, pdf._fit, MARGIN, A4_H - MARGIN - 4)
        if scoring_only:
            return pdf.finish()
        pdf.new_page()
    if act.get("insurer_known"):
        pdf.para(act["insurer"], size=10, bold=True, color=GRAY, gap=2)
    pdf.para(act["title"], size=15, bold=True, gap=4)
    pdf.kv([(r["label"], str(r["value"])) for r in act["header"]], label_w=150)
    pdf.rule()
    label_w, note_w = 140, 160
    for s in act["sections"]:
        pdf.heading(f"{s['n']}. {s['title']}")
        for p in s["paragraphs"]:
            pdf.para(p, size=10)
        if s["rows"]:
            pdf.table([("", label_w, "l"), ("", CONTENT_W - label_w - note_w, "l"), ("", note_w, "l")],
                      [[r["label"], str(r["value"]), r.get("note") or ""] for r in s["rows"]], header=False)
        for p in s.get("source_lines") or []:
            pdf.para(p, size=9, color=GRAY, gap=2)
        for li in s.get("lists") or []:
            tb = li.get("table")
            if tb and tb.get("rows"):
                pdf.ensure(90)               # заголовок таблицы не остаётся внизу страницы без строк
            else:
                pdf.ensure(45)               # заголовок списка — хотя бы с первой строкой
            pdf.para(li["title"], size=10, bold=True, gap=2)
            if tb and tb.get("rows"):
                w = tb.get("widths") or [100 // len(tb["columns"])] * len(tb["columns"])
                cols = [(c, CONTENT_W * x / sum(w), "l") for c, x in zip(tb["columns"], w)]
                pdf.table(cols, [[str(c) for c in r] for r in tb["rows"]], size=8, header=True)
                for p in li.get("notes") or []:
                    pdf.para(p, size=9, gap=2)
                for p in li.get("sources") or []:
                    pdf.para(p, size=8, color=GRAY, gap=2)
                continue
            for it in li["items"]:
                pdf.bullet(it, size=9.5)
    pdf.space(6)
    pdf.ensure(40)
    pdf.rule()
    pdf.para(act["footer"], size=10, bold=True)
    return pdf.finish()
