"""Проверяемые настройки процесса. Импорт не открывает базу и не создаёт файлы."""
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = PROJECT_ROOT / "app"
FRONTEND_DIR = PROJECT_ROOT / "frontend"


class ConfigurationError(ValueError):
    """Некорректная настройка запуска; сообщение не содержит секретных значений."""


def read_env_files(paths) -> dict[str, str]:
    """Первый файл главнее; одинаковые правила для процесса и подключений."""
    values = {}
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values.setdefault(key, value.strip().strip('\"').strip("'"))
    return values


def boolean(env: Mapping[str, str], name: str, default=False) -> bool:
    value = env.get(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name}: ожидается 0 или 1")


@dataclass(frozen=True)
class Settings:
    storage_dir: Path
    dev_mode: bool = False
    no_background: bool = False
    background_mode: str = "embedded"
    auto_migrate: bool = True
    demo_seed: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        values = os.environ if env is None else env
        mode = values.get("SURVEYOR_BACKGROUND_MODE", "embedded").strip().lower()
        if mode not in {"embedded", "external"}:
            raise ConfigurationError("SURVEYOR_BACKGROUND_MODE: ожидается embedded или external")
        path = Path(values.get("STORAGE_DIR") or PROJECT_ROOT / "data").expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        # DATABASE_URL исторически игнорировался; теперь неподдерживаемое подключение видно сразу.
        if values.get("DATABASE_URL", "").strip():
            raise ConfigurationError("DATABASE_URL не поддерживается: используйте SQLite и STORAGE_DIR")
        return cls(
            storage_dir=path.resolve(),
            dev_mode=boolean(values, "SURVEYOR_DEV"),
            no_background=boolean(values, "SURVEYOR_NO_BACKGROUND"),
            background_mode=mode,
            auto_migrate=boolean(values, "SURVEYOR_AUTO_MIGRATE", True),
            demo_seed=boolean(values, "DEMO_SEED"),
        )


def load_environment() -> None:
    """Вызывать в точке входа до импорта модулей с путями хранения."""
    values = read_env_files((PROJECT_ROOT / ".env", PROJECT_ROOT / ".secrets.env"))
    for key, value in values.items():
        os.environ.setdefault(key, value)
