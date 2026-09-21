"""
Резервная копия: база и рабочие файлы в один zip-архив.

Запуск:  sandbox\\.venv\\Scripts\\python.exe tools/backup.py
Из сервера: POST /deploy/backup (страница /admin/deploy).

Что попадает в архив: data/surveyor.db (снимок через SQLite backup — копия целостна даже
во время работы сервера и в режиме WAL, когда часть данных ещё в файле surveyor.db-wal),
а также data/uploads, data/photos, data/reports, data/inbox, data/parsed.
Сами архивы (data/backups) в копию не кладём.

Папка данных — та же, что у сервера: STORAGE_DIR (постоянный диск на Railway) или data/ проекта.
В архиве пути всегда вида data/..., поэтому копию с сервера можно развернуть локально и наоборот.

Имя файла: <папка данных>/backups/surveyor-ГГГГ-ММ-ДД-ЧЧММ.zip
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# как в app/db.py: без этого на Railway копировалась бы база из образа, а не с постоянного диска
DATA = Path(os.environ["STORAGE_DIR"]) if os.environ.get("STORAGE_DIR") else ROOT / "data"
DB = DATA / "surveyor.db"
BACKUPS = DATA / "backups"

# что кладём в архив, кроме базы
FOLDERS = ("uploads", "photos", "reports", "inbox", "parsed")
SKIP = {"backups"}
MAX_FILE_MB = 100          # очень крупные файлы пропускаем, чтобы копия не раздувалась


def db_snapshot(dest: Path) -> bool:
    """Целостный снимок базы: у SQLite для этого есть собственный механизм backup."""
    if not DB.exists():
        return False
    # не mode=ro: база в режиме WAL на чтение открывается только при наличии файла -shm
    src = sqlite3.connect(str(DB), timeout=10)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    return True


def arcname(f: Path, root: Path = None) -> str:
    """Путь внутри архива: всегда data/<путь от папки данных>."""
    return "data/" + str(f.relative_to(root or DATA)).replace("\\", "/")


def folder_root(folder: str) -> Path:
    """Где лежит папка: на постоянном диске (фото, загрузки) или в data/ проекта (отчёты НАПП —
    их кладут скрипты tools/ рядом с кодом)."""
    return DATA if (DATA / folder).exists() else ROOT / "data"


def make(note: str = "") -> dict:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    archive = BACKUPS / f"surveyor-{stamp}.zip"
    n = 2
    while archive.exists():            # вторая копия в ту же минуту (например, «перед восстановлением»)
        archive = BACKUPS / f"surveyor-{stamp}-{n}.zip"   # не должна затирать первую
        n += 1
    n, skipped = 0, []
    tmp = Path(tempfile.mkdtemp(prefix="bkp_"))
    try:
        snap = tmp / "surveyor.db"
        has_db = db_snapshot(snap)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            if has_db:
                z.write(snap, "data/surveyor.db")
                n += 1
            for folder in FOLDERS:
                root = folder_root(folder)
                base = root / folder
                if not base.exists() or folder in SKIP:
                    continue
                for f in base.rglob("*"):
                    if not f.is_file():
                        continue
                    name = arcname(f, root)
                    if f.stat().st_size > MAX_FILE_MB * 1024 * 1024:
                        skipped.append(name)
                        continue
                    z.write(f, name)
                    n += 1
            z.writestr("КОПИЯ.txt", f"Резервная копия ИИ-сюрвейера INSON\nсоздана: {datetime.now():%d.%m.%Y %H:%M}\n"
                                    f"файлов: {n}\nпримечание: {note or '—'}\n"
                                    f"восстановление: python tools/restore.py {archive.name} --да\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    size_mb = round(archive.stat().st_size / 1024 / 1024, 2)
    return {"ok": True, "file": archive.name, "path": str(archive), "size_mb": size_mb,
            "files": n, "skipped": skipped, "db_included": has_db}


def listing() -> list:
    if not BACKUPS.exists():
        return []
    out = []
    for f in sorted(BACKUPS.glob("*.zip"), reverse=True):
        st = f.stat()
        out.append({"file": f.name, "size_mb": round(st.st_size / 1024 / 1024, 2),
                    "created": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    res = make(" ".join(sys.argv[1:]))
    print(f"Копия готова: {res['path']}")
    print(f"Файлов: {res['files']}, размер: {res['size_mb']} МБ")
    if res["skipped"]:
        print("Пропущены слишком большие файлы:", ", ".join(res["skipped"]))
