"""
Полки офиса агентов: что стоит на книжных стеллажах на экране /office.

Зачем. Заказчик попросил, чтобы на полках стояли те источники, которыми агенты
пользуются на самом деле. Источники лежат в library/catalog.json (его собирает
tools/library_build.py). Здесь каталог только группируется по шести полкам —
ничего не выдумываем и ничего не добавляем от себя.

Отдаём плоский список: [{group, name, файл}], где group — название полки на экране.
"""
import json
from pathlib import Path

from fastapi import APIRouter

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "library" / "catalog.json"

# папка каталога -> полка на экране
SHELVES = [
    ("Законы", ("01_Законодательство/01_Законы",)),
    ("Акты НАПП", ("01_Законодательство/02_Акты_регуляторов",)),
    ("Компания", ("02_Компания_INSON", "04_Образцы_документов")),
    ("Статистика", ("03_Рынок_НАПП",)),
    ("Методология", ("05_Методология",)),
    ("Заметки", ("06_Заметки_проекта",)),
]


def _shelf_of(group: str) -> str:
    for shelf, folders in SHELVES:
        for f in folders:
            if group == f or group.startswith(f + "/"):
                return shelf
    return "Заметки"


router = APIRouter()


@router.get("/office/shelves")
def office_shelves():
    """Корешки книг для полок офиса: название документа и файл, если он есть."""
    if not CATALOG.exists():
        return []
    try:
        items = json.loads(CATALOG.read_text(encoding="utf-8"))
    except Exception:
        return []
    order = {name: i for i, (name, _) in enumerate(SHELVES)}
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        out.append({
            "group": _shelf_of(it.get("group") or ""),
            "name": it["name"],
            "файл": it.get("файл"),
        })
    out.sort(key=lambda r: (order.get(r["group"], 99), r["name"]))
    return out
