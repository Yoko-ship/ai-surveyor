"""Текст Word 97–2003 через antiword; макросы и внешние ссылки не исполняются.

Парсер работает отдельным процессом без секретов окружения, с ограничениями
времени, памяти, вывода и параллелизма. DOC не отправляется языковой модели.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_INPUT = 15 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
TIMEOUT = 5
_SLOTS = threading.BoundedSemaphore(2)


class DocReadError(ValueError):
    """Неподдерживаемый, повреждённый или защищённый документ; без его содержимого."""


def read_doc_bytes(blob: bytes, timeout: float = TIMEOUT) -> str:
    if not blob.startswith(OLE_SIGNATURE) or len(blob) > MAX_INPUT:
        raise DocReadError("invalid_doc")
    binary = shutil.which("antiword")
    if not binary:
        raise DocReadError("reader_unavailable")
    if not _SLOTS.acquire(blocking=False):
        raise DocReadError("reader_busy")
    try:
        # Явный список: токены приложения не наследуются парсером.
        env = {"PATH": os.defpath, "LANG": "C.UTF-8"}
        if os.environ.get("ANTIWORDHOME"):
            env["ANTIWORDHOME"] = os.environ["ANTIWORDHOME"]
        with tempfile.TemporaryDirectory(prefix="doc-reader-") as work, tempfile.TemporaryFile() as out:
            try:
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), binary],
                    input=blob, stdout=out, stderr=subprocess.DEVNULL, cwd=work,
                    env=env, timeout=max(0.001, min(TIMEOUT, timeout)), check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise DocReadError("reader_timeout") from exc
            if result.returncode:
                raise DocReadError("unreadable_doc")
            out.seek(0)
            text = out.read(MAX_OUTPUT + 1)
            if len(text) > MAX_OUTPUT:
                raise DocReadError("output_limit")
            try:
                return text.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise DocReadError("invalid_encoding") from exc
    except OSError as exc:
        raise DocReadError("reader_unavailable") from exc
    finally:
        _SLOTS.release()


def read_doc(path: Path) -> tuple:
    from . import docparse as D
    D.tick()
    with Path(path).open("rb") as stream:
        blob = stream.read(MAX_INPUT + 1)
    text = read_doc_bytes(blob, D.remaining_seconds(TIMEOUT))
    D.tick()
    # Таблицы остаются текстом; структуру и значения ячеек не выдумываем.
    return text, []


if __name__ == "__main__":
    # Ограничения ставятся в дочернем процессе: preexec_fn в многопоточном
    # FastAPI небезопасен. exec сохраняет PID, timeout убивает сам парсер.
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if sys.platform == "linux":
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    os.execve(sys.argv[1], [sys.argv[1], "-t", "-m", "UTF-8.txt", "-w", "0", "-"], os.environ)
