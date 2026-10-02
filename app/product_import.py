"""
Импорт продуктов из Excel (записка заказчика, 02.10.2026). Два шага: предпросмотр → применить.

  GET  /reference/products/template.xlsx — шаблон
  POST /reference/products/import         — файл .xlsx ≤ 2 МБ → предпросмотр и token (администратор)
  POST /reference/products/import/apply   — {token} → запись (администратор)

Что пишет «применить» — то же, что форма «Новый продукт» в админке (POST /admin/products, app/main.py):
строка products, состав классов product_classes, журнал audit «продукт» (по нему app/refsync.py не затирает
ручные правки). Отличия от формы — ради правил проекта:
  * ничего не удаляется: у существующего продукта классы из файла только ДОБАВЛЯЮТСЯ;
  * базовая ставка — новая версия тарифа (tariff_versions, уровень «компания», дата вступления из файла)
    плюс строка product_rate_versions; старые версии не трогаются (правило № 9);
  * минимальная ставка и тип ставки — через app/min_rates.save (та же версия, что при ручной правке);
  * дата вступления — не раньше сегодняшней: прошлые расчёты должны воспроизводиться.
Базовая ставка. Движок пока НЕ читает её из версий: он берёт текст тарифа products.rate_text. Поэтому
базовая ставка принимается только там, где её честно можно записать в rate_text прямо сейчас:
  * дата вступления — сегодня (rate_text не версионируется, будущую ставку применить нечем);
  * продукт из одного класса: у многоклассового продукта act_engine.class_min читает rate_text как минимум
    второго и следующих классов — «базовая» молча подменила бы минимум;
  * у существующего продукта текст простой («0,05%»), а не по частям («имущ. 0,1% · отв. 0,5%»).
Иначе строка — с ошибкой «укажите минимальную ставку или дату сегодня». Вместе с rate_text пишется версия
(tariff_versions + product_rate_versions) — для истории; расчёт и акт эти версии своими не считают
(min_rates.current_version).

Строки с ошибками не записываются; остальные записываются (ok_count в предпросмотре).
"""
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from . import db, min_rates, xlsx_import as xi
from .auth import require

router = APIRouter()

ADMIN = "админ"
FILE_MAX = 2 * 1024 * 1024
MAX_ROWS = 2000
STATUSES = ("тест", "действует")
STATUTORY = "нормативный акт"
CODE_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-_.]{0,19}")
LEVEL = "компания"
SOURCE = "import"

HEAD = ["Код", "Название", "Классы (через запятую)", "Базовая ставка %", "Минимальная ставка %",
        "Тип ставки (годовая|фиксированная)", "Дата вступления", "Статус (тест|действует)"]
HEAD_HINT = "«Код», «Название», «Классы»"

PREVIEWS = xi.Previews("products")


def _match(h: str) -> Optional[str]:
    if h.startswith("класс"):
        return "classes"
    if h.startswith("код"):
        return "code"
    if h.startswith("назван") or h.startswith("наименован"):
        return "name"
    if h.startswith("базов"):
        return "rate_pct"
    if h.startswith("миним"):
        return "min_rate_pct"
    if h.startswith("тип"):
        return "rate_type"
    if h.startswith("дата"):
        return "effective_from"
    if h.startswith("статус"):
        return "status"
    return None


# --------------------------------------------------------------------------- #
#  Проверка строк
# --------------------------------------------------------------------------- #

def _classes(v) -> tuple:
    """«8, 13» → ["8", "13"]. Число 8.13 — неоднозначно (Excel мог съесть запятую): ошибка."""
    if v is None or v == "":
        return [], None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if float(v) == int(v):
            return [str(int(v))], None
        return [], "классы записаны числом — укажите их текстом через запятую, например «8,13»"
    out = []
    for part in re.split(r"[,;\s]+", str(v).strip()):
        if part and part not in out:
            out.append(part)
    return out, None


def _pct(v, what: str, errors: list) -> Optional[float]:
    n, err = xi.as_number(v)
    if err:
        errors.append(f"{what}: {err}")
        return None
    if n is None:
        return None
    if not (0 < n <= min_rates.MAX_PCT):
        errors.append(f"{what}: число больше 0 и не больше {min_rates.MAX_PCT:g} (в процентах, 0,5 — это 0,5%)")
        return None
    return round(n, 6)


def _simple_rate_text(text: str) -> bool:
    if not (text or "").strip():
        return True
    from .act_engine import part_rates_from_text
    return set(part_rates_from_text(text, [])) == {"*"}


def check_rows(con, raw_rows: list, today: Optional[date] = None) -> dict:
    """Строки файла → предпросмотр. Ничего не пишет."""
    today = today or date.today()
    known_classes = {r["code"] for r in db.rows(con, "SELECT code FROM classes")}
    items, seen = [], set()
    for raw in raw_rows:
        v = raw["values"]
        errors, warnings, changes = [], [], []
        code = xi.product_code(v.get("code"))
        name = xi.as_text(v.get("name"), 200)
        classes, cerr = _classes(v.get("classes"))
        rate = _pct(v.get("rate_pct"), "Базовая ставка", errors)
        min_rate = _pct(v.get("min_rate_pct"), "Минимальная ставка", errors)
        rt_raw = v.get("rate_type")
        rate_type = min_rates.parse_rate_type(rt_raw)
        if rate_type is None:
            errors.append("Тип ставки: «годовая» или «фиксированная»")
        status = xi.as_text(v.get("status"), 20).lower()
        eff, derr = xi.as_date(v.get("effective_from"))

        if not code:
            errors.append("Код: пустой")
        elif not CODE_RE.fullmatch(code):
            errors.append("Код: буквы и цифры, до 20 знаков")
        elif code in seen:
            errors.append("Код: продукт уже встречается в файле выше")
        seen.add(code)
        if cerr:
            errors.append("Классы: " + cerr)
        bad = [c for c in classes if c not in known_classes]
        if bad:
            errors.append("Классы: нет в справочнике — " + ", ".join(bad))
        if status and status not in STATUSES:
            errors.append("Статус: «тест» или «действует»")
        if derr:
            errors.append("Дата вступления: " + derr)
        elif eff and eff < today:
            errors.append("Дата вступления: не раньше сегодняшней — прошлые расчёты должны воспроизводиться")
        elif eff and eff.year > today.year + 10:
            errors.append("Дата вступления: не дальше чем на 10 лет вперёд")
        eff_s = (eff or today).isoformat()
        if rate is not None and min_rate is not None and min_rate > rate:
            warnings.append("минимальная ставка больше базовой")

        prod = db.rows(con, "SELECT * FROM products WHERE code=?", code) if code else []
        prod = prod[0] if prod else None
        action = "create" if not prod else "update"
        have = []
        if not prod:
            if not name:
                errors.append("Название: для нового продукта обязательно")
            if not classes:
                errors.append("Классы: для нового продукта нужен хотя бы один класс")
            changes.append("новый продукт")
        else:
            if prod["pricing_mode"] == STATUTORY and (rate is not None or min_rate is not None):
                errors.append("Обязательный вид: ставку устанавливает нормативный акт")
            if name and name != prod["name"]:
                changes.append("название")
            if status and status != prod["status"]:
                changes.append(f"статус: {prod['status']} → {status}")
            have = [r["class_code"] for r in db.rows(
                con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", code)]
            add = [c for c in classes if c not in have]
            if add:
                changes.append("добавить классы: " + ", ".join(add))
            kept = [c for c in have if classes and c not in classes]
            if kept:
                warnings.append("классы " + ", ".join(kept) + " есть в справочнике, в файле нет — остаются "
                                "(из справочника ничего не удаляется)")
        if rate is not None:
            n_classes = len(set(classes) | set(have))
            why = None
            if eff_s != today.isoformat():
                why = "базовая ставка с будущей датой пока не поддерживается"
            elif n_classes > 1:
                why = ("базовая ставка для продукта из нескольких классов пока не поддерживается "
                       "(текст тарифа служит минимумом классов)")
            elif prod and not _simple_rate_text(prod["rate_text"]):
                why = "базовая ставка для тарифа по частям пока не поддерживается"
            if why:
                errors.append(f"Базовая ставка: {why} — укажите минимальную ставку или дату сегодня")
            elif not prod or _rate_text(rate) != (prod["rate_text"] or "").replace(" ", ""):
                changes.append(f"базовая ставка {rate:g}% с {eff_s}: текст тарифа «{_rate_text(rate)}»")
        if min_rate is not None:
            cur = min_rates.on_date(con, code, eff_s) if prod else None
            if not cur or abs(float(cur["pct"]) - min_rate) > 1e-9 or cur["rate_type"] != rate_type \
                    or eff_s > today.isoformat() and cur["effective_from"] != eff_s:
                changes.append(f"минимальная ставка {min_rate:g}% с {eff_s} (новая версия тарифа)")
        elif rt_raw not in (None, "") and rate is None:
            warnings.append("тип ставки без ставки не применяется — укажите базовую или минимальную ставку")
        if prod and not changes and not errors:
            action = "skip"
        items.append({"row": raw["row"], "product_code": code, "name": name or (prod or {}).get("name") or "",
                      "classes": classes, "rate_pct": rate, "min_rate_pct": min_rate,
                      "rate_type": rate_type or "", "effective_from": eff_s,
                      "status": status or ((prod or {}).get("status") if prod else "тест"),
                      "action": "skip" if errors else action, "changes": changes,
                      "errors": errors, "warnings": warnings})
    ok = sum(1 for it in items if not it["errors"])
    return {"items": items, "ok_count": ok, "error_count": len(items) - ok,
            "to_write": sum(1 for it in items if it["action"] in ("create", "update"))}


# --------------------------------------------------------------------------- #
#  Запись
# --------------------------------------------------------------------------- #

def _rate_text(pct: float) -> str:
    return f"{pct:g}".replace(".", ",") + "%"


def _apply_item(con, it: dict, who: str, today: str) -> dict:
    code = it["product_code"]
    prod = db.rows(con, "SELECT * FROM products WHERE code=?", code)
    prod = prod[0] if prod else None
    done = {"product_code": code, "action": it["action"], "versions": []}
    if not prod:
        rate_text = _rate_text(it["rate_pct"]) if it["rate_pct"] is not None else ""
        con.execute("INSERT INTO products (code, name, rate_text, commission_text, commission_pct, pricing_mode,"
                    " is_general, status, note) VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, it["name"], rate_text, "", None, "ставка", 0, it["status"] or "тест",
                     "импорт из Excel"))
        for i, cl in enumerate(it["classes"], start=1):
            con.execute("INSERT INTO product_classes (product_code, class_code, part_no) VALUES (?,?,?)",
                        (code, cl, i))
        rate_before = None
    else:
        rate_before = prod["rate_text"]
        sets = {}
        if it["name"] and it["name"] != prod["name"]:
            sets["name"] = it["name"]
        if it["status"] and it["status"] != prod["status"]:
            sets["status"] = it["status"]
        if sets:
            con.execute("UPDATE products SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE code=?",
                        tuple(sets.values()) + (code,))
        have = [r["class_code"] for r in db.rows(
            con, "SELECT class_code FROM product_classes WHERE product_code=? ORDER BY part_no", code)]
        part = len(have)
        for cl in it["classes"]:
            if cl not in have:
                part += 1
                con.execute("INSERT INTO product_classes (product_code, class_code, part_no) VALUES (?,?,?)",
                            (code, cl, part))
    # журнал «продукт» — признак ручной правки для app/refsync.py (как у формы «Новый продукт»)
    db.audit(con, who, "продукт", code, {"source": "импорт из Excel", "action": it["action"],
                                         "name": it["name"], "classes": it["classes"], "status": it["status"],
                                         "rate_pct": it["rate_pct"], "min_rate_pct": it["min_rate_pct"],
                                         "rate_type": it["rate_type"], "effective_from": it["effective_from"]})
    eff = it["effective_from"]
    if it["rate_pct"] is not None and any(c.startswith("базовая ставка") for c in it["changes"]):
        name = f"Базовая ставка — продукт {code}"
        cur = con.execute("INSERT INTO tariff_versions (level, name, document_ref, effective_from) VALUES (?,?,?,?)",
                          (LEVEL, name, "импорт продуктов из Excel", eff))
        vid = cur.lastrowid
        con.execute("INSERT INTO product_rate_versions (tariff_version_id, product_code, base_rate_pct, rate_type,"
                    " rate_text_before, source, created_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
                    (vid, code, it["rate_pct"], it["rate_type"] or "annual", rate_before, SOURCE, db.now(), who))
        db.audit(con, who, "новая версия тарифов", f"version:{vid}",
                 {"level": LEVEL, "name": name, "effective_from": eff, "base_rate_pct": it["rate_pct"],
                  "source": SOURCE})
        if prod:          # условия (сегодня, один класс, простой текст) проверены в check_rows
            con.execute("UPDATE products SET rate_text=? WHERE code=?", (_rate_text(it["rate_pct"]), code))
        done["versions"].append({"kind": "base_rate", "tariff_version_id": vid, "effective_from": eff})
    if it["min_rate_pct"] is not None and any(c.startswith("минимальная ставка") for c in it["changes"]):
        saved = min_rates.save(con, code, {"min_rate_pct": it["min_rate_pct"], "rate_type": it["rate_type"] or "annual",
                                           # примечание пустое, как у импорта минимальных ставок без
                                           # примечания: оно выводится в акт на любом языке (source_label)
                                           "effective_from": eff, "note": ""},
                               who, min_rates.SRC_IMPORT)
        done["versions"].append({"kind": "min_rate", "tariff_version_id": saved["version_id"],
                                 "effective_from": eff})
    return done


def apply_rows(con, raw_rows: list, who: str, file_name: str = "") -> dict:
    """Проверяет строки заново по текущей базе и записывает те, что без ошибок."""
    min_rates.ensure(con)
    pv = check_rows(con, raw_rows)
    today = date.today().isoformat()
    done = [_apply_item(con, it, who, today) for it in pv["items"]
            if not it["errors"] and it["action"] in ("create", "update")]
    if done:
        db.reference_changed(con)          # калькулятор и акт сразу видят новые продукты и ставки
    db.audit(con, who, "импорт продуктов из Excel", "products",
             {"файл": file_name[:120], "строк": len(pv["items"]),
              "создано": sum(1 for d in done if d["action"] == "create"),
              "обновлено": sum(1 for d in done if d["action"] == "update"),
              "с ошибками": pv["error_count"]})
    return {"ok": True, "created": sum(1 for d in done if d["action"] == "create"),
            "updated": sum(1 for d in done if d["action"] == "update"),
            "skipped": len(pv["items"]) - len(done), "error_count": pv["error_count"],
            "items": done, "preview": pv}


# --------------------------------------------------------------------------- #
#  Маршруты
# --------------------------------------------------------------------------- #

def template_bytes() -> bytes:
    return xi.template(
        "Продукты", HEAD,
        ["0999", "Пример продукта — удалите строку", "8", 0.5, 0.3, "годовая",
         date.today().strftime("%d.%m.%Y"), "тест"],
        ["Импорт продуктов",
         "",
         "1. Одна строка — один продукт. Код — как в справочнике (например, 0832). Есть код — продукт обновится,",
         "   нет — будет создан.",
         "2. Классы — текстом через запятую: 8,9 или 13. У существующего продукта классы только добавляются.",
         "3. Ставки — в процентах: 0,5 значит 0,5%. Можно оставить пустыми.",
         "4. Базовая ставка пока принимается только с датой вступления СЕГОДНЯ и только для продукта из",
         "   одного класса с простым тарифом («0,05%»): она сразу становится текстом тарифа продукта.",
         "   Будущая дата, несколько классов или тариф по частям — строка с ошибкой; укажите минимальную ставку.",
         "5. Минимальная ставка — с датой не раньше сегодняшней, новой версией тарифа; прежние версии",
         "   остаются, старые расчёты воспроизводятся. Тип ставки: годовая (премия = сумма × ставка × дни / 365)",
         "   или фиксированная (на весь срок).",
         "6. Статус: тест (виден только андеррайтеру) или действует. Пусто у нового продукта — тест.",
         "7. Сначала — предпросмотр с ошибками по строкам; записываются только строки без ошибок.",
         "8. Из справочников ничего не удаляется."],
        [10, 40, 22, 18, 22, 32, 18, 24])


@router.get("/reference/products/template.xlsx")
def products_template():
    return Response(template_bytes(), media_type=xi.XLSX_MIME,
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''products_template.xlsx"})


def _parse_and_check(data: bytes) -> tuple:
    """Разбор файла и проверка строк — в пуле потоков, не в цикле событий."""
    sheet = xi.read_sheet(data, _match, {"code", "name", "classes"}, MAX_ROWS, HEAD_HINT)
    with db.tx() as con:
        min_rates.ensure(con)
        return sheet, check_rows(con, sheet["rows"])


@router.post("/reference/products/import")
async def products_import(file: UploadFile = File(...), user: dict = Depends(require(ADMIN))):
    data = await xi.check_upload(file, FILE_MAX)
    sheet, pv = await run_in_threadpool(_parse_and_check, data)
    token = PREVIEWS.put(user["login"], {"rows": sheet["rows"], "file_name": file.filename or ""})
    return pv | {"token": token, "file_name": file.filename or "",
                 "ignored_columns": sheet["unknown"], "expires_min": xi.TOKEN_TTL_SEC // 60}


@router.post("/reference/products/import/apply")
def products_import_apply(body: dict = Body(...), user: dict = Depends(require(ADMIN))):
    token = str((body or {}).get("token") or "")
    if not token:
        raise HTTPException(422, "Нужен token из предпросмотра")
    payload = PREVIEWS.take(token, user["login"])
    with db.tx() as con:
        out = apply_rows(con, payload["rows"], user["login"], payload["file_name"])
    if not out["created"] and not out["updated"] and out["error_count"]:
        raise HTTPException(422, "Нет строк без ошибок — ничего не записано")
    return out
