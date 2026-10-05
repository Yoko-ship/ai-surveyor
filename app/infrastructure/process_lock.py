"""Межпроцессная блокировка: ОС освобождает её и при аварийном завершении."""
import os
import errno
import time
from pathlib import Path


class AlreadyLockedError(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self, timeout: float = 0):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    if time.monotonic() < deadline:
                        time.sleep(min(.05, max(0, deadline - time.monotonic())))
                        continue
                    handle.close()
                    raise AlreadyLockedError(f"Хранилище занято другим процессом ({self.path.name})") from exc
                handle.close()
                raise
        self._file = handle

    def release(self):
        if self._file is not None:
            self._file.close()
            self._file = None
