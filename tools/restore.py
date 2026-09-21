"""
Восстановление из резервной копии. ОПАСНАЯ ОПЕРАЦИЯ: заменяет рабочую базу.

Запуск:
    python tools/restore.py                       — показать список копий
    python tools/restore.py <имя_файла.zip>       — показать, что внутри, и НИЧЕГО не менять
    python tools/restore.py <имя_файла.zip> --да  — восстановить (с подтверждением)

Из сервера: POST /deploy/restore с полем confirm=true (без него — отказ).

Перед заменой текущее состояние само уходит в копию с пометкой «перед восстановлением»,
чтобы ошибочное восстановление можно было откатить.
Сервер на время восстановления лучше остановить: файл базы занят.
"""
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))
import backup as backup_tool                                      # noqa: E402

DATA = backup_tool.DATA                    # папка данных сервера (STORAGE_DIR или data/)
BACKUPS = DATA / "backups"


def _safe(name: str) -> Path:
    """Только файл из data/backups: путь извне не принимаем."""
    p = (BACKUPS / Path(name).name).resolve()
    if p.parent != BACKUPS.resolve() or not p.exists():
        raise FileNotFoundError("Копия не найдена: " + Path(name).name)
    return p


def inspect(name: str) -> dict:
    p = _safe(name)
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        note = z.read("КОПИЯ.txt").decode("utf-8", "replace") if "КОПИЯ.txt" in names else ""
    return {"file": p.name, "files": len(names), "has_db": "data/surveyor.db" in names,
            "size_mb": round(p.stat().st_size / 1024 / 1024, 2), "note": note,
            "sample": names[:20]}


def _restore_db(z: zipfile.ZipFile, item: str):
    """
    База — не перезаписью файла, а штатной копией SQLite поверх рабочей базы. В режиме WAL рядом
    лежат surveyor.db-wal и -shm: если просто заменить файл, SQLite применит к новой базе старый
    журнал и испортит её. Копия SQLite заменяет содержимое целиком и корректно при открытых соединениях.
    """
    tmp = Path(tempfile.mkdtemp(prefix="restore_"))
    try:
        snap = tmp / "surveyor.db"
        with z.open(item) as src, snap.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        DATA.mkdir(parents=True, exist_ok=True)
        s = sqlite3.connect(str(snap))
        d = sqlite3.connect(str(backup_tool.DB), timeout=30)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restore(name: str, confirm: bool = False, who: str = "админ") -> dict:
    """Возвращает отчёт. Без confirm=True ничего не делает — это защита от случайного нажатия."""
    info = inspect(name)
    if not confirm:
        return {"ok": False, "applied": False,
                "reason": "Восстановление заменит текущую базу. Повторите с подтверждением (confirm=true)",
                "info": info}
    if not info["has_db"]:
        return {"ok": False, "applied": False, "reason": "В архиве нет файла базы data/surveyor.db", "info": info}
    before = backup_tool.make("автоматически перед восстановлением")
    p = _safe(name)
    restored = 0
    with zipfile.ZipFile(p) as z:
        for item in z.namelist():
            if item == "КОПИЯ.txt" or not item.startswith("data/"):
                continue
            if item == "data/surveyor.db":
                _restore_db(z, item)
                restored += 1
                continue
            rel = item[len("data/"):]
            root = backup_tool.folder_root(rel.split("/", 1)[0]).resolve()
            target = (root / rel).resolve()
            if not str(target).startswith(str(root)):                # защита от путей вида ../..
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, target.open("wb") as dst:
                dst.write(src.read())
            restored += 1
    return {"ok": True, "applied": True, "restored_files": restored, "from": p.name,
            "rollback_file": before["file"],
            "reason": "Восстановлено. Перезапустите сервер, чтобы он увидел новую базу"}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    yes = "--да" in sys.argv or "--yes" in sys.argv
    if not args:
        items = backup_tool.listing()
        print("Доступные копии (data/backups):" if items else "Копий пока нет — сделайте: python tools/backup.py")
        for it in items:
            print(f"  {it['file']}  {it['size_mb']} МБ  {it['created']}")
        sys.exit(0)
    name = args[0]
    if not yes:
        info = inspect(name)
        print(f"Копия {info['file']}: файлов {info['files']}, размер {info['size_mb']} МБ, "
              f"база внутри: {'да' if info['has_db'] else 'НЕТ'}")
        print(info["note"])
        print("Ничего не изменено. Чтобы восстановить, повторите команду с --да")
        sys.exit(0)
    answer = input("Текущая база будет заменена. Введите «да» для подтверждения: ").strip().lower()
    if answer not in ("да", "yes", "y"):
        print("Отменено.")
        sys.exit(1)
    res = restore(name, confirm=True)
    print(res["reason"])
    if res.get("rollback_file"):
        print("Откат на прежнее состояние: python tools/restore.py", res["rollback_file"], "--да")
