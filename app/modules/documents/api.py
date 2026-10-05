"""Загрузка документов страхового запроса."""
from fastapi import APIRouter, Depends, File, UploadFile
from app.auth import current_user
from app.modules.documents.storage import store_document
router = APIRouter()

@router.post("/requests/{rid}/documents")
def upload_document(rid: int, doc_name: str, file: UploadFile = File(...),
                    user: dict = Depends(current_user)):
    return store_document(rid, doc_name, file.filename or "document", file.file, user)
