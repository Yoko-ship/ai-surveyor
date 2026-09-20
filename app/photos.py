"""
Файлы по объекту страхования: фотографии и документы (техпаспорт, кадастр).
Загрузка, список, отдача файла, удаление. Разбор документов — в app/docparse.py.

Файлы кладём в data/photos/<request_id>/, в базе храним только путь — так база остаётся лёгкой
и переносится на PostgreSQL без изменений.

Данные из учётной системы недоверенные, поэтому на входе проверяем всё:
  - тип: для фотографии объекта только image/jpeg, image/png, image/webp; для документа
    дополнительно application/pdf. Тип определяем не по заголовку клиента, а по сигнатуре
    первых байтов файла (заголовок Content-Type подделывается);
  - размер: не больше 10 МБ;
  - имя: клиентское имя не используем как имя файла (в нём бывают '..' и разделители пути) —
    генерируем своё, клиентское сохраняем отдельным полем только для показа.
"""
import secrets
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import db
from .docparse import ALL_KINDS, DOC_KINDS, KIND_PHOTO as DEFAULT_KIND, mark_received, parse_photo

ROOT = Path(__file__).resolve().parent.parent
PHOTOS_DIR = db.DATA_DIR / "photos"
router = APIRouter()

MAX_BYTES = 10 * 1024 * 1024          # ~10 МБ на файл
ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
# PDF принимаем только как документ (техпаспорт, кадастр), фотографией объекта он быть не может
PDF_MIME = "application/pdf"
DOC_ALLOWED = dict(ALLOWED, **{PDF_MIME: ".pdf"})

# Сигнатуры: определяем формат по содержимому, а не по тому, что сказал клиент.
MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def sniff_mime(blob: bytes) -> Optional[str]:
    """Тип картинки по первым байтам. None — формат не из разрешённых."""
    for sig, mime in MAGIC:
        if blob.startswith(sig):
            return mime
    # WebP: RIFF....WEBP
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:5] == b"%PDF-":
        return PDF_MIME
    return None


def _check_request(con, rid: int):
    if not db.rows(con, "SELECT id FROM requests WHERE id=?", rid):
        raise HTTPException(404, "Запрос не найден")


def _store(con, rid: int, blob: bytes, client_name: str, who: str, note: str = None,
           doc_kind: str = DEFAULT_KIND) -> dict:
    """Проверяет файл и сохраняет его на диск и в базу. Ошибку не глотаем — пишем в журнал."""
    if doc_kind not in ALL_KINDS:
        raise HTTPException(400, "Вид файла: " + ", ".join(ALL_KINDS))
    allowed = DOC_ALLOWED if doc_kind != DEFAULT_KIND else ALLOWED
    if not blob:
        raise HTTPException(400, "Пустой файл")
    if len(blob) > MAX_BYTES:
        db.audit(con, who, "фото отклонено: размер", f"request:{rid}",
                 {"filename": client_name, "size_bytes": len(blob), "limit": MAX_BYTES})
        raise HTTPException(413, f"Файл больше {MAX_BYTES // (1024 * 1024)} МБ")
    mime = sniff_mime(blob)
    if mime not in allowed:
        db.audit(con, who, "файл отклонён: формат", f"request:{rid}",
                 {"filename": client_name, "doc_kind": doc_kind, "mime": mime})
        raise HTTPException(415, "Для «%s» принимаются: %s"
                            % (doc_kind, ", ".join(sorted(allowed))))

    folder = PHOTOS_DIR / str(rid)
    folder.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(8) + allowed[mime]     # своё имя: клиентскому не доверяем
    dest = folder / name
    dest.write_bytes(blob)

    rel = dest.relative_to(ROOT).as_posix()
    shown = Path(str(client_name or "")).name[:120] or None   # только для показа, путь отрезан
    cur = con.execute("INSERT INTO photos (request_id, path, filename, mime, size_bytes, uploaded_at,"
                      " uploaded_by, note, doc_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                      (rid, rel, shown, mime, len(blob), db.now(), who, note, doc_kind))
    db.audit(con, who, "загружен файл объекта", f"request:{rid}",
             {"photo_id": cur.lastrowid, "mime": mime, "size_bytes": len(blob), "doc_kind": doc_kind})
    return {"id": cur.lastrowid, "path": rel, "filename": shown, "mime": mime,
            "size_bytes": len(blob), "doc_kind": doc_kind}


@router.post("/requests/{rid}/photos")
async def upload_photos(rid: int, request: Request, files: List[UploadFile] = File(None),
                        who: str = "api", note: str = None):
    """
    Загрузка фотографий объекта. Два способа:
      - multipart/form-data, поле files (можно несколько файлов);
      - одиночный файл сырым телом: Content-Type: image/jpeg и заголовок X-Filename.
    """
    saved = []
    with db.tx() as con:
        _check_request(con, rid)
        if files:
            for f in files:
                saved.append(_store(con, rid, await f.read(), f.filename, who, note))
        else:
            blob = await request.body()
            if not blob:
                raise HTTPException(400, "Файлы не переданы")
            saved.append(_store(con, rid, blob, request.headers.get("X-Filename", ""), who, note))
    return {"ok": True, "saved": saved}


@router.get("/requests/{rid}/photos")
def list_photos(rid: int):
    with db.tx() as con:
        _check_request(con, rid)
        return db.rows(con, "SELECT id, request_id, path, filename, mime, size_bytes, uploaded_at,"
                            " uploaded_by, note, doc_kind, parse_status, parsed_at"
                            " FROM photos WHERE request_id=? AND doc_kind=? ORDER BY id", rid, DEFAULT_KIND)


def _photo(con, photo_id: int) -> dict:
    r = db.rows(con, "SELECT * FROM photos WHERE id=?", photo_id)
    if not r:
        raise HTTPException(404, "Фото не найдено")
    return r[0]


@router.get("/photos/{photo_id}")
def get_photo(photo_id: int):
    with db.tx() as con:
        p = _photo(con, photo_id)
    full = ROOT / p["path"]
    # путь из базы всё равно проверяем: файл обязан лежать внутри data/photos
    if PHOTOS_DIR not in full.resolve().parents or not full.exists():
        raise HTTPException(404, "Файл не найден на диске")
    return FileResponse(full, media_type=p["mime"], filename=p["filename"] or full.name)


@router.delete("/photos/{photo_id}")
def delete_photo(photo_id: int, who: str = "api"):
    with db.tx() as con:
        p = _photo(con, photo_id)
        full = ROOT / p["path"]
        removed = False
        if PHOTOS_DIR in full.resolve().parents and full.exists():
            full.unlink()
            removed = True
        con.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        db.audit(con, who, "удалено фото", f"request:{p['request_id']}",
                 {"photo_id": photo_id, "file_removed": removed})
    return {"ok": True, "id": photo_id, "file_removed": removed}


# --------------------------------------------------------------------------- #
# Документы объекта: техпаспорт и кадастровый документ
# --------------------------------------------------------------------------- #
# Лежат в той же таблице photos, отличаются полем doc_kind. Сразу после загрузки файл
# разбирается (app/docparse.py), результат сохраняется, пункт чек-листа отмечается полученным.


@router.post("/requests/{rid}/documents/upload")
async def upload_document_file(rid: int, request: Request, doc_kind: str,
                               files: List[UploadFile] = File(None), who: str = "api",
                               note: str = None, parse: bool = True,
                               with_reg_no: bool = False):
    """
    Загрузка документа объекта: PDF или фотография.
      doc_kind — 'техпаспорт' или 'кадастр'.
    Способы те же, что у фотографий: multipart (поле files) или сырое тело с X-Filename.
    После загрузки документ разбирается, из него берутся характеристики объекта.
    """
    if doc_kind not in DOC_KINDS:
        raise HTTPException(400, "doc_kind: " + ", ".join(DOC_KINDS))
    saved = []
    with db.tx() as con:
        _check_request(con, rid)
        blobs = []
        if files:
            for f in files:
                blobs.append((await f.read(), f.filename))
        else:
            body = await request.body()
            if not body:
                raise HTTPException(400, "Файлы не переданы")
            blobs.append((body, request.headers.get("X-Filename", "")))
        for blob, fname in blobs:
            rec = _store(con, rid, blob, fname, who, note, doc_kind)
            if parse:
                rec["разбор"] = parse_photo(con, rec["id"], doc_kind, who=who,
                                            with_reg_no=with_reg_no)
            else:
                rec["чек_лист"] = mark_received(con, rid, doc_kind, rec["path"], None, who)
            saved.append(rec)
    return {"ok": True, "saved": saved}


@router.get("/requests/{rid}/documents/files")
def list_document_files(rid: int):
    """Загруженные документы объекта (без фотографий)."""
    with db.tx() as con:
        _check_request(con, rid)
        return db.rows(con, "SELECT id, request_id, filename, mime, size_bytes, uploaded_at,"
                            " uploaded_by, note, doc_kind, parse_status, parsed_at"
                            " FROM photos WHERE request_id=? AND doc_kind<>? ORDER BY id",
                       rid, DEFAULT_KIND)
