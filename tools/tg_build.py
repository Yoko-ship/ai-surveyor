r"""
Сборка мини-приложения: frontend/tg/ (tg.html, tg.css, js/*.js) → app/tg.html (app/tgpage.py).

Запуск из корня проекта:
    sandbox\.venv\Scripts\python.exe tools\tg_build.py           — собрать
    sandbox\.venv\Scripts\python.exe tools\tg_build.py --check   — только проверить, что app/tg.html свежий
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import tgpage  # noqa: E402

if __name__ == "__main__":
    if "--check" in sys.argv:
        ok = tgpage.is_fresh()
        print("app/tg.html свежий" if ok else "app/tg.html устарел: запустите tools/tg_build.py")
        sys.exit(0 if ok else 1)
    changed = tgpage.write_page()
    print("app/tg.html собран" if changed else "app/tg.html уже совпадает с исходниками")
