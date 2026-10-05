"""Приём вложения: права, ограниченный поток, уникальное имя, откат при ошибке."""
from pathlib import Path
import uuid

from fastapi import HTTPException
from app import access, db

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


def store_document(rid, doc_name, filename, stream, user):
    with db.tx() as con:
        access.ensure_can_open(con, user, rid)
    folder = db.DATA_DIR / "uploads" / str(rid)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    if not suffix[1:].isalnum() or len(suffix) > 12:
        suffix = ""
    dest = folder / (uuid.uuid4().hex + suffix)
    try:
        with dest.open("xb") as output:
            size = 0
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > MAX_DOCUMENT_BYTES:
                    raise HTTPException(413, "Документ превышает 20 МБ")
                output.write(chunk)
        with db.tx() as con:
            access.ensure_can_open(con, user, rid)
            con.execute("INSERT INTO documents (request_id, doc_name, file_path, received) VALUES (?,?,?,1)",
                        (rid, doc_name, db.stored_path(dest)))
            db.audit(con, user.get("login") or "api", "загружен документ", f"request:{rid}", {"doc": doc_name})
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return {"ok": True, "stored": db.stored_path(dest)}
