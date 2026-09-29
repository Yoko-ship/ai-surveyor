"""
Документ Word (.docx) средствами стандартной библиотеки: zipfile + WordprocessingML. Без пакетов.

    doc = Docx()
    doc.title("СЮРВЕЙЕРСКИЙ АКТ")         # крупный заголовок
    doc.heading("1. Объект", level=1)
    doc.para("Текст **с выделением**", bold=False, color="555555", size=20)
    doc.bullet("пункт списка")
    doc.table([["Поле", "Значение"], ["Марка", "XCMG"]], widths=[3000, 6600])
    data = doc.to_bytes()                  # готовый .docx

Размеры шрифта — в полупунктах (как в WordprocessingML): 22 = 11 pt. Ширины — в twip (1/20 pt),
ширина текста на A4 с полями 2 см ≈ 9 600. Управляющие символы, которых не бывает в XML, вырезаются.
"""
import io
import re
import zipfile
from xml.sax.saxutils import escape

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
TEXT_WIDTH = 9638                  # A4 (11906) минус поля 1134 × 2
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def clean(text) -> str:
    """Текст для XML: без запрещённых символов, спецсимволы экранированы."""
    return escape(_BAD_XML.sub("", str(text if text is not None else "")))


def _runs(text: str, bold=False, size=None, color=None, italic=False) -> str:
    out = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", str(text or ""))):
        if not part:
            continue
        b = bold or i % 2 == 1
        pr = ("<w:b/>" if b else "") + ("<w:i/>" if italic else "") + \
             (f'<w:color w:val="{color}"/>' if color else "") + \
             (f'<w:sz w:val="{int(size)}"/><w:szCs w:val="{int(size)}"/>' if size else "")
        # перевод строки внутри абзаца — <w:br/>
        pieces = part.split("\n")
        for j, piece in enumerate(pieces):
            if j:
                out.append(f"<w:r><w:rPr>{pr}</w:rPr><w:br/></w:r>")
            if piece:
                out.append(f'<w:r><w:rPr>{pr}</w:rPr><w:t xml:space="preserve">{clean(piece)}</w:t></w:r>')
    return "".join(out)


def _para(text, *, bold=False, size=None, color=None, before=0, after=120, indent=0, keep=False,
          align=None, italic=False) -> str:
    ppr = f'<w:spacing w:before="{int(before)}" w:after="{int(after)}"/>'
    if indent:
        ppr += f'<w:ind w:left="{int(indent)}" w:hanging="280"/>'
    if keep:
        ppr += "<w:keepNext/>"
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    return f"<w:p><w:pPr>{ppr}</w:pPr>{_runs(text, bold, size, color, italic)}</w:p>"


class Docx:
    def __init__(self, lang: str = "ru-RU", font: str = "Arial"):
        self.parts = []
        self.lang = lang
        self.font = font

    # ---------- блоки ----------
    def title(self, text: str, color: str = "1D2C8F"):
        self.parts.append(_para(text, bold=True, size=30, color=color, after=160, align="center", keep=True))

    def heading(self, text: str, level: int = 1, color: str = "1D2C8F"):
        size = {1: 26, 2: 23}.get(level, 22)
        self.parts.append(_para(text, bold=True, size=size, color=color, before=240, after=100, keep=True))

    def para(self, text: str, bold=False, size=None, color=None, italic=False, align=None, after=120):
        self.parts.append(_para(text, bold=bold, size=size, color=color, italic=italic, align=align, after=after))

    def bullet(self, text: str, size=None, color=None):
        self.parts.append(_para("•\t" + str(text or ""), size=size, color=color, indent=420, after=60))

    def table(self, rows: list, widths: list = None, header: bool = True, size: int = 20):
        """rows — список строк (списков ячеек); первая строка — заголовок, если header=True."""
        if not rows:
            return
        n = max(len(r) for r in rows)
        widths = list(widths or [TEXT_WIDTH // n] * n)[:n]
        border = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="B9BFD6"/>'
                         for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
        x = [f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{border}</w:tblBorders>'
             f'<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="90" w:type="dxa"/>'
             f'<w:right w:w="90" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
             + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"]
        for ri, r in enumerate(rows):
            r = (list(r) + [""] * n)[:n]
            head = header and ri == 0
            x.append("<w:tr>" + ("<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if head
                                 else "<w:trPr><w:cantSplit/></w:trPr>"))
            for c, w in zip(r, widths):
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="EEF0F9"/>' if head else ""
                x.append(f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shade}</w:tcPr>'
                         + _para(c, bold=head, size=size, before=30, after=30) + "</w:tc>")
            x.append("</w:tr>")
        x.append("</w:tbl>" + _para("", after=60))
        self.parts.append("".join(x))

    def rule(self):
        self.parts.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" '
                          'w:color="B9BFD6"/></w:pBdr><w:spacing w:after="120"/></w:pPr></w:p>')

    # ---------- сборка ----------
    def document_xml(self) -> str:
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W_NS}><w:body>'
                + "".join(self.parts) +
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" '
                'w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>')

    def to_bytes(self) -> bytes:
        f = clean(self.font)
        styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W_NS}><w:docDefaults>'
                  f'<w:rPrDefault><w:rPr><w:rFonts w:ascii="{f}" w:hAnsi="{f}" w:cs="{f}" w:eastAsia="{f}"/>'
                  f'<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="{clean(self.lang)}"/></w:rPr></w:rPrDefault>'
                  '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr>'
                  '</w:pPrDefault></w:docDefaults></w:styles>')
        types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                 'officedocument.wordprocessingml.document.main+xml"/>'
                 '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
                 'officedocument.wordprocessingml.styles+xml"/>'
                 '</Types>')
        rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    'relationships/styles" Target="styles.xml"/></Relationships>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", types)
            z.writestr("_rels/.rels", rels)
            z.writestr("word/document.xml", self.document_xml())
            z.writestr("word/styles.xml", styles)
            z.writestr("word/_rels/document.xml.rels", doc_rels)
        return buf.getvalue()
