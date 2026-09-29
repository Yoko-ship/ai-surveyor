"""
Сюрвейерский акт, лёгкая версия (app/act.py, app/act_engine.py, app/docx_lite.py) — ТЗ 2.0 от 29.09.2026.

Запуск из корня (свой ASGI-клиент, живой сервер не трогаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_act.py

Всё — во временной копии базы (tests/tmpdb.py) и во временной папке файлов.
Дополнения 29.09.2026 (проверки 21–27): сценарии PML/EML/MFL, разбор документов, франшиза, рекомендации. Сеть и модель подменяются:
llm.chat_raw отдаёт заготовленный ответ, llm._post бросает исключение (любой выход в сеть = ошибка теста).
Ставки в проверках берутся из справочника копии базы (engine.rate_for / engine.min_rate), а не из головы.
"""
import asyncio
import io
import json as _json
import os
import re
import secrets
import shutil
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from xml.dom import minidom

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.pop("SURVEYOR_DEV", None)      # guard проверяем целиком, без режима разработчика
os.environ["SURVEYOR_NO_BACKGROUND"] = "1"

import pymupdf                            # noqa: E402

from tmpdb import temp_db                 # noqa: E402
from app import act, act_engine as ae, db, guest, llm   # noqa: E402
from app.engine import Input, min_rate, rate_for        # noqa: E402
from app.main import app                  # noqa: E402

passed, failed = 0, 0
COOKIES = {}
HEADERS = []                              # дополнительные заголовки (Bearer администратора)

CRANE_MUST = {"product_code": "0318", "sum_insured": 2_945_000_000, "object_value": 3_100_000_000,
              "region": "Ташкентская область"}
CRANE_OPT = {"location": "open_area", "losses_3y": {"count": 0, "small_count": 0, "amount": 0}}
CRANE_TYPE = "Спецтехника — автокран"


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, str(extra)[:400])


# ------------------------------------------------------------------ ASGI-клиент

def _send(method, path, params, headers, payload, raw=False):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": urlencode(params or {}, encoding="utf-8").encode(),
             "headers": headers + list(HEADERS), "client": ("203.0.113.9", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": [], "headers": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"], out["headers"] = msg["status"], msg.get("headers") or []
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    for k, v in out["headers"]:
        if k.lower() == b"set-cookie":
            pair = v.decode("latin-1").split(";")[0]
            name, _, value = pair.partition("=")
            COOKIES[name.strip()] = value.strip()
    body = b"".join(out["chunks"])
    if raw:
        return out["status"], body, dict((k.decode().lower(), v.decode("latin-1")) for k, v in out["headers"])
    text = body.decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(text)
    except ValueError:
        return out["status"], text


def _cookie_hdr():
    return [(b"cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()).encode())] if COOKIES else []


def call(method, path, body=None, params=None, raw=False):
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    hdrs = [(b"host", b"test"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())] + _cookie_hdr()
    return _send(method, path, params, hdrs, payload, raw)


def upload(files, fields=None):
    boundary = "----insonact"
    parts = []
    for k, v in (fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for name, mime, blob in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
                     f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    payload = b"".join(parts)
    hdrs = [(b"host", b"test"), (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            (b"content-length", str(len(payload)).encode())] + _cookie_hdr()
    return _send("POST", "/act/photos", None, hdrs, payload)


def image(color=(200, 150, 0), kind="png", w=320, h=240) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("jpg" if kind == "jpg" else "png")


# ------------------------------------------------------------------ подмена модели

CALLS = []
REPLY = {"text": None}


def fake_chat_raw(purpose, messages, max_tokens=700, temperature=0.2, files=None, timeout=None, retries=None):
    CALLS.append({"purpose": purpose, "messages": messages, "files": files, "temperature": temperature,
                  "timeout": timeout, "retries": retries})
    if REPLY.get("sleep"):
        import time as _t
        _t.sleep(REPLY["sleep"])
    text = REPLY["text"](messages) if callable(REPLY["text"]) else REPLY["text"]
    return {"text": text, "ok": bool(text), "notes": [], "ms": 1, "reason": None if text else "пустой ответ"}


def no_network(*a, **kw):
    raise AssertionError("тест не должен ходить в сеть")


ORIG = {}


def model_on(on=True, files=True):
    llm.enabled = lambda: on
    llm.supports_files = lambda: on and files


CRANE_REPLY = "```json\n" + _json.dumps({
    "files": [{"n": 1, "view": "front"}, {"n": 2, "view": "left"}, {"n": 3, "view": "plate"},
              {"n": 4, "view": "document", "document_kind": "лист технических параметров"}],
    "object_kind": "truck_crane", "class_hint": "special_machinery", "condition": "new",
    "fields": [
        {"key": "object_type", "value": "автокран", "source": "photo", "file": 1},
        {"key": "brand", "value": "XCMG", "source": "marking", "file": 2},
        {"key": "model", "value": "QY50K5D", "source": "marking", "file": 2},
        {"key": "model", "value": "XCMG QY50K5D", "source": "document", "file": 4},
        {"key": "year", "value": "2026", "source": "document", "file": 4},
        {"key": "manufacture_date", "value": "2026-03", "source": "plate", "file": 3},
        {"key": "serial_no", "value": "LXGCPA393TA006921", "source": "plate", "file": 3},
        {"key": "serial_no", "value": "LXGCPA393TA006921", "source": "document", "file": 4},
        {"key": "manufacturer", "value": "Xuzhou Construction Machinery Group Co., Ltd.", "source": "plate",
         "file": 3},
        {"key": "curb_mass", "value": "36 170 кг", "source": "plate", "file": 3},
        {"key": "curb_mass", "value": "38 600 кг", "source": "document", "file": 4},
        {"key": "engine_power", "value": "248 кВт", "source": "plate", "file": 3},
        {"key": "engine_power", "value": "251 кВт", "source": "document", "file": 4},
        {"key": "location", "value": "открытая площадка", "source": "photo", "file": 1},
        {"key": "color", "value": None, "source": "photo", "file": 1},
        {"key": "owner", "value": "что-то чужое", "source": "document", "file": 4},
    ],
    "damages": []}, ensure_ascii=False) + "\n```"

CRANE_FILES = [("front.jpg", "image/jpeg", image(kind="jpg")), ("left.png", "image/png", image((250, 200, 0))),
               ("plate.png", "image/png", image((90, 90, 90))), ("sheet.png", "image/png", image((255, 255, 255)))]


def expected_rate(con, level_adj: float, product="0318", cls="3", otype=CRANE_TYPE):
    """Ручной расчёт по справочнику копии базы: ставка продукта по тарифной политике (если её нет —
    техническая ставка без коэффициентов), поправка по уровню, минимум."""
    ref = db.load_reference(con)
    mr = min_rate(ref, product)
    base = mr.get("company")
    if base is None:
        base = rate_for(ref, Input(product_code=product, class_code=cls, object_type=otype, value_amount=1,
                                   sum_insured=1, factors={}))["gross_pct"]
    floor = mr["floor"]
    applied = round(max(base * (1 + level_adj / 100), floor or 0), 4)
    return round(base, 4), floor, applied


def all_text(a: dict) -> str:
    out = [a["title"], a["footer"]]
    for s in a["sections"]:
        out += [s["title"]] + s["paragraphs"]
        out += [f"{r['label']} {r['value']} {r.get('note') or ''}" for r in s["rows"]]
        for li in s.get("lists") or []:
            out += [li["title"]] + li["items"]
    return "\n".join(out)


# ------------------------------------------------------------------ 1. пример заказчика

def check_crane():
    print("1. Пример заказчика: автокран XCMG QY50K5D")
    REPLY["text"] = CRANE_REPLY
    model_on(True)
    CALLS.clear()
    st, b = upload(CRANE_FILES, {"lang": "ru", "product_code": "0318"})
    ok("фото приняты", st == 200 and b.get("ok"), (st, b))
    ok("гостю выдана cookie", bool(COOKIES.get("gid")))
    ok("один запрос к модели со всеми файлами", len(CALLS) == 1 and len(CALLS[0]["files"]) == 4, len(CALLS))
    ok("низкая температура", CALLS[0]["temperature"] <= 0.2)
    sys_prompt = CALLS[0]["messages"][0]["content"] + CALLS[0]["messages"][1]["content"]
    ok("в инструкции «не выдумывай» и запрет на данные людей",
       "не выдумывай" in sys_prompt and "не извлекай" in sys_prompt and "посторонних машин" in sys_prompt)
    ok("маскировка ПД не портит инструкцию", llm.mask_pd(sys_prompt) == sys_prompt)
    ok("имена файлов в модель не уходят",
       all(f["name"].startswith("file ") for f in CALLS[0]["files"]) and "front.jpg" not in sys_prompt)
    ok("PNG пережат в JPEG перед отправкой", all(f["mime"] == "image/jpeg" for f in CALLS[0]["files"]))
    views = {f["index"]: f["view"] for f in b["files"]}
    ok("ракурсы распознаны (сопоставление по номеру файла в запросе)",
       views == {1: "front", 2: "left", 3: "plate", 4: "document"}, views)
    ok("распознавание — одна попытка с таймаутом 20 с", CALLS[0]["retries"] == 0 and CALLS[0]["timeout"] == 20,
       (CALLS[0]["retries"], CALLS[0]["timeout"]))
    keys = [(r["key"], r["source"]) for r in b["recognized"]]
    ok("оба значения массы вернулись (табличка и документ)",
       ("curb_mass", "plate") in keys and ("curb_mass", "document") in keys, keys)
    ok("пустые и неизвестные поля отброшены",
       not any(k == "color" for k, _ in keys) and not any(k == "owner" for k, _ in keys))
    ok("название изготовителя не принято за ФИО",
       any(r["key"] == "manufacturer" for r in b["recognized"]), keys)
    ok("у каждого значения источник словами и «проверьте»",
       all(r["source_label"] and r["check_label"] == "проверьте" for r in b["recognized"]))
    miss = {v["code"] for v in b["missing_views"]}
    ok("не хватает ракурсов: сзади, правый борт, счётчик", miss == {"back", "right", "odometer"}, miss)
    ok("предупреждение про данные людей и тестовый сервер",
       "данными людей" in b["warning"] and "тестовый" in b["warning"])
    ok("ai = true", b["ai"] is True)
    ok("вид объекта — автокран", (b.get("object_kind") or {}).get("code") == "truck_crane")
    sid = b["session"]

    CALLS.clear()
    st, a = call("POST", "/act/make", {"session": sid, "lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT,
                                       "recognized": b["recognized"]})
    ok("/act/make к модели не обращается", not CALLS, len(CALLS))
    ok("акт сформирован", st == 200 and a.get("ok"), (st, a))
    ok("пять разделов", [s["n"] for s in a["sections"]] == [1, 2, 3, 4, 5])
    ok("строка о подтверждении андеррайтером",
       a["footer"] == "Акт сформирован ИИ-сюрвейером, подлежит подтверждению андеррайтером")
    ok("шапка акта", a["title"] == "СЮРВЕЙЕРСКИЙ АКТ ПРЕДСТРАХОВОГО ОСМОТРА" and a["number"] and a["date"])
    ok("уровень риска по правилу — низкий (1 повышает, 3 снижают)",
       a["risk"]["level"] == "low" and a["risk"]["up"] == 1 and a["risk"]["down"] == 3, a["risk"])
    ok("признаки с объяснением", len(a["risk"]["factors"]) == 4 and all(f["text"] for f in a["risk"]["factors"]))
    with db.tx() as con:
        base, floor, applied = expected_rate(con, 0)
    ok("базовая ставка = ставка продукта по тарифной политике",
       abs(a["rate"]["base_pct"] - base) < 1e-9, (a["rate"]["base_pct"], base))
    ok("поправка низкого уровня 0 %", a["rate"]["adj_pct"] == 0)
    ok("итоговая ставка совпадает с ручным расчётом", abs(a["rate"]["applied_pct"] - applied) < 1e-9,
       (a["rate"]["applied_pct"], applied))
    ok("минимум продукта из справочника", a["rate"]["min_pct"] == floor, (a["rate"]["min_pct"], floor))
    prem = round(applied / 100 * CRANE_MUST["sum_insured"])
    ok("премия = сумма × ставка", a["premium"]["amount"] == prem, (a["premium"]["amount"], prem))
    ok("калибровка помечена", a["rate"]["calibrated"] == 0 and a["risk"]["calibrated"] == 0)
    ok("тип объекта для ставки — автокран", a["rate"]["object_type"] == CRANE_TYPE, a["rate"]["object_type"])
    ok("версия тарифа сохранена", a["rate"]["tariff_version_id"] is not None)
    ok("сумма ≈95 % стоимости — «в норме»",
       a["value"]["verdict"] == "normal" and abs(a["value"]["ratio_pct"] - 95.0) < 0.01, a["value"])
    ok("текст раздела 3 «В норме»", "В норме" in a["value"]["text"], a["value"]["text"])
    ok("«Франшиза не требуется»", a["franchise"]["needed"] is False and a["franchise"]["text"] ==
       "Франшиза не требуется", a["franchise"])
    codes = {c["code"] for c in a["clauses"]}
    ok("оговорки спецтехники", {"sp_attachments_storage", "sp_territory", "sp_reinspection"} <= codes, codes)
    ok("оговорки помечены экспертными", all(c["expert"] for c in a["clauses"]))
    ok("повторный осмотр через 12 месяцев или 2 000 моточасов",
       any("12 месяцев" in c["text"] and "2 000 моточасов" in c["text"] for c in a["clauses"]))
    dk = {d["key"]: d for d in a["discrepancies"]}
    ok("расхождение по массе и мощности", set(dk) == {"curb_mass", "engine_power"}, list(dk))
    ok("серийный номер и модель (с маркой в документе) не считаются расхождением",
       "serial_no" not in dk and "model" not in dk)
    s5 = all_text({"title": "", "footer": "", "sections": [a["sections"][4]]})
    ok("в заключении: масса 36 170 кг на табличке и 38 600 кг в документе",
       "на табличке 36 170 кг" in s5 and "в документе 38 600 кг" in s5, s5[:600])
    ok("в заключении: мощность 248 кВт и 251 кВт", "248 кВт" in s5 and "251 кВт" in s5)
    ok("приоритет у документа с печатью производителя", "печатью производителя" in s5)
    ok("решение: принять с оговорками", a["decision"]["code"] == "accept_with_clauses", a["decision"])
    ok("андеррайтеру: снять расхождение и дозапросить фото",
       any("Снаряжённая масса" in c for c in a["decision"]["checks"])
       and any("правый борт" in c for c in a["decision"]["checks"]), a["decision"]["checks"])
    s1 = {r["label"]: r for r in a["sections"][0]["rows"]}
    ok("в разделе 1 заводской номер из документа",
       s1["Заводской (серийный) номер"]["value"] == "LXGCPA393TA006921"
       and "из документа" in s1["Заводской (серийный) номер"]["note"], s1.get("Заводской (серийный) номер"))
    ok("в разделе 1 масса из документа и вариант с таблички",
       s1["Снаряжённая масса"]["value"] == "38 600 кг" and "36 170 кг" in s1["Снаряжённая масса"]["note"])
    ok("чего нет — «данные недоступны»", s1["Цвет"]["value"] == "данные недоступны", s1.get("Цвет"))
    ok("цвет в списке недоступных данных", "Цвет" in a["missing"], a["missing"])
    text = all_text(a)
    ok("цифры в тексте совпадают с расчётом",
       act.pct(applied, "ru") in text and act.money(prem, "ru") in text)
    return a, sid


# ------------------------------------------------------------------ 2. сумма и стоимость

def check_value():
    print("2. Недострахование и превышение")
    st, a = call("POST", "/act/make", {"lang": "ru", "must": dict(CRANE_MUST, sum_insured=2_000_000_000),
                                       "optional": CRANE_OPT})
    ok("< 90 % — недострахование, ГК ст. 936",
       st == 200 and a["value"]["verdict"] == "under" and a["value"]["legal_ref"] == "ГК РУз, ст. 936"
       and "пропорциональной" in a["value"]["text"], a.get("value"))
    ok("андеррайтеру — про пропорциональную выплату",
       any("пропорциональн" in c for c in a["decision"]["checks"]))
    st, a = call("POST", "/act/make", {"lang": "ru", "must": dict(CRANE_MUST, sum_insured=3_500_000_000),
                                       "optional": CRANE_OPT})
    ok("> 100 % — превышение, ГК ст. 938",
       st == 200 and a["value"]["verdict"] == "over" and a["value"]["legal_ref"] == "ГК РУз, ст. 938"
       and "снизить" in a["value"]["text"], a.get("value"))
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, price_new=3_000_000_000, purchase_year=
                                                        date.today().year - 2)})
    dep = a["value"]["depreciated"]
    ok("ориентир с износом: транспорт 20 % в год", dep and dep["wear_pct_per_year"] == 20
       and dep["value"] == round(3_000_000_000 * (1 - 0.2 * 2)), dep)
    st, a = call("POST", "/act/make", {"lang": "ru", "must": dict(CRANE_MUST, sum_insured="12abc"),
                                       "optional": dict(CRANE_OPT, year=1800)})
    ok("мусор во вводе — 422 с полями", st == 422 and "sum_insured" in a.get("errors", {})
       and "year" in a.get("errors", {}), a)
    with db.tx() as con:
        row = db.rows(con, "SELECT detail FROM audit WHERE action='акт: ошибка ввода' ORDER BY id DESC LIMIT 1")
    ok("ошибка ввода — в журнале, без значений", row and "sum_insured" in row[0]["detail"]
       and "12abc" not in row[0]["detail"], row)


# ------------------------------------------------------------------ 3. франшиза

def check_franchise():
    print("3. Франшиза — только при основании")
    from app.risk_analytics import load_thresholds
    with db.tx() as con:
        th = load_thresholds(con)
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, losses_3y={"count": 2, "small_count": 2})})
    fr = a["franchise"]
    band = th["franchise_by_level"][ae.RA_LEVEL[a["risk"]["level"]]]
    cap = th["franchise_class_caps"].get("3")
    hi = min(band[1], cap) if cap is not None else band[1]
    ok("2 мелких убытка — франшиза советуется", fr["needed"] and any(g["code"] == "fr_g_small_losses"
                                                                     for g in fr["grounds"]), fr)
    ok("размер — вилка из порогов (не выдуман)", fr.get("size") and fr["size"]["to_pct"] == hi
       and fr["size"]["from_pct"] == min(band[0], hi), (fr.get("size"), band, cap))
    ok("размер определяет андеррайтер", "андеррайтер" in fr["text"], fr["text"])
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, want_lower_premium=True)})
    ok("просьба клиента — основание", a["franchise"]["needed"] and
       [g["code"] for g in a["franchise"]["grounds"]] == ["fr_g_client"], a["franchise"])
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT})
    ok("без оснований — «Франшиза не требуется»", a["franchise"]["text"] == "Франшиза не требуется")
    # обязательный вид: строительно-монтажные риски, обязательное (0820) — тариф по ПКМ, без поправок
    must = {"product_code": "0820", "sum_insured": 1_000_000_000, "object_value": 1_000_000_000,
            "region": "Ташкент"}
    st, a = call("POST", "/act/make", {"lang": "ru", "must": must,
                                       "optional": {"losses_3y": {"count": 3, "small_count": 3},
                                                    "want_lower_premium": True, "location": "construction"}})
    with db.tx() as con:
        rt = db.rows(con, "SELECT rate_text FROM products WHERE code='0820'")[0]["rate_text"]
    act_rate = float(re.search(r"(\d+(?:,\d+)?)\s*%", rt).group(1).replace(",", "."))
    ok("обязательный вид: ставка по акту без поправок",
       st == 200 and a["rate"]["mode"] == "statutory" and a["rate"]["applied_pct"] == act_rate
       and a["rate"]["adj_pct"] == 0, a.get("rate"))
    ok("обязательный вид: премия по ставке акта",
       a["premium"]["amount"] == round(act_rate / 100 * 1_000_000_000), a["premium"])
    ok("обязательный вид: франшиза не применяется",
       a["franchise"]["needed"] is False and "обязательный вид" in a["franchise"]["text"], a["franchise"])
    must["product_code"] = "1002"            # ОСГО владельцев ТС: в тексте тарифа числа нет
    st, a = call("POST", "/act/make", {"lang": "ru", "must": must, "optional": {}})
    ok("обязательный вид без числа в справочнике — ставка не выдумана",
       st == 200 and a["rate"]["applied_pct"] is None and a["premium"]["amount"] is None, a.get("rate"))


# ------------------------------------------------------------------ 4. продукт без ставки

def check_no_rate():
    print("4. Продукт «по программе» — ставка не определена")
    must = dict(CRANE_MUST, product_code="0321")
    st, a = call("POST", "/act/make", {"lang": "ru", "must": must, "optional": CRANE_OPT})
    ok("режим undefined, цифр нет", st == 200 and a["rate"]["mode"] == "undefined"
       and a["rate"]["applied_pct"] is None and a["rate"]["base_pct"] is None
       and a["premium"]["amount"] is None, a.get("rate"))
    rows = {r["label"]: r["value"] for r in a["sections"][3]["rows"]}
    ok("в акте: «ставка не определена — нужен расчёт андеррайтера»",
       rows.get("Рекомендуемый тариф") == "ставка не определена — нужен расчёт андеррайтера", rows)
    ok("премия — «данные недоступны»", rows.get("Страховая премия") == "данные недоступны")
    ok("андеррайтеру — определить ставку", any("Определить ставку" in c for c in a["decision"]["checks"]))
    ok("не «принять» без оговорок", a["decision"]["code"] != "accept")


# ------------------------------------------------------------------ 5. модель недоступна / мусор

def check_no_model():
    print("5. Модель недоступна или вернула мусор")
    model_on(False)
    CALLS.clear()
    st, b = upload(CRANE_FILES[:2], {"lang": "ru"})
    ok("фото сохранены, ai = false", st == 200 and b["ai"] is False and b["session"], b)
    ok("к модели не обращались", not CALLS)
    ok("честное сообщение", "недоступно" in b["message"], b["message"])
    st, a = call("POST", "/act/make", {"session": b["session"], "lang": "ru", "must": CRANE_MUST,
                                       "optional": CRANE_OPT})
    s2 = all_text({"title": "", "footer": "", "sections": [a["sections"][1]]})
    ok("акт сформирован без модели", st == 200 and len(a["sections"]) == 5)
    ok("раздел осмотра честный: распознать не удалось, осмотр не проводился",
       "не удалось" in s2 and "осмотр не проводился" in s2, s2)
    ok("андеррайтеру — проверить фото вручную", any("вручную" in c for c in a["decision"]["checks"]))

    model_on(True)
    REPLY["text"] = "Извините, я не могу помочь с этим запросом."
    st, b = upload(CRANE_FILES[:1], {"lang": "ru"})
    ok("не-JSON — ai = false и причина", st == 200 and b["ai"] is False and "не по схеме" in b["message"], b)
    REPLY["text"] = '{"files": "не список", "fields": 5}'
    st, b = upload(CRANE_FILES[:1], {"lang": "ru"})
    ok("JSON не по схеме — ai = false", b["ai"] is False, b)
    st, a = call("POST", "/act/make", {"session": b["session"], "lang": "ru", "must": CRANE_MUST,
                                       "optional": CRANE_OPT})
    ok("акт после мусора формируется", st == 200 and a["inspection"]["done"] is False)

    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT})
    s2 = all_text({"title": "", "footer": "", "sections": [a["sections"][1]]})
    ok("без фото — «осмотр не проводился»", "Осмотр не проводился" in s2 and "осмотр не проводился" in s2, s2)
    ok("без фото — документы не представлены повышают риск",
       any(f["code"] == "f_docs_up" for f in a["risk"]["factors"]))

    st, b = upload([("x.webp", "image/webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ")], {"lang": "ru"})
    ok("WEBP вежливо отклонён", st == 422 and b["rejected"] and "JPG, PNG или PDF" in b["rejected"][0]["error"], b)
    st, b = upload([(f"{i}.png", "image/png", image()) for i in range(11)], {"lang": "ru"})
    ok("больше 10 файлов — 413", st == 413, st)


# ------------------------------------------------------------------ 6. ПД

def check_pd():
    print("6. Персональные данные")
    model_on(True)
    name = "Иванов Иван Иванович"
    REPLY["text"] = _json.dumps({
        "files": [{"n": 1, "view": "document"}],
        "fields": [{"key": "location", "value": name, "source": "document", "file": 1},
                   {"key": "manufacturer", "value": "Петров П. С.", "source": "document", "file": 1},
                   {"key": "serial_no", "value": "AA1234567", "source": "document", "file": 1},
                   {"key": "model", "value": "QY50K5D", "source": "document", "file": 1}],
        "damages": [{"what": "вмятина, владелец Сидоров Сидор", "file": 1}]}, ensure_ascii=False)
    st, b = upload([("Иванов Иван.jpg", "image/jpeg", image(kind="jpg"))], {"lang": "ru"})
    dump = _json.dumps(b, ensure_ascii=False)
    ok("ФИО из ответа модели отброшено", name not in dump and "Петров" not in dump and "Сидоров" not in dump, dump)
    ok("паспорт отброшен", "AA1234567" not in dump)
    ok("остальное сохранилось", any(r["key"] == "model" for r in b["recognized"]))
    ok("имя файла замаскировано", "Иванов" not in b["files"][0]["name"], b["files"])
    st, a = call("POST", "/act/make", {"session": b["session"], "lang": "ru", "must": CRANE_MUST,
                                       "optional": CRANE_OPT,
                                       "recognized": b["recognized"] + [
                                           {"key": "location", "value": "Сидоров Сидор Сидорович",
                                            "source": "input"}]})
    ok("ФИО во вводе сотрудника тоже отброшено", "Сидоров" not in _json.dumps(a, ensure_ascii=False))
    with db.tx() as con:
        rows = db.rows(con, "SELECT result_json, files_json FROM act_uploads WHERE id=?", b["session"])
        audit = db.rows(con, "SELECT who, action, entity, detail FROM audit WHERE action LIKE 'акт%'")
        calls = db.rows(con, "SELECT purpose, error FROM llm_calls")
        acts = db.rows(con, "SELECT act_json FROM acts")
    stored = _json.dumps(rows, ensure_ascii=False) + _json.dumps(acts, ensure_ascii=False)
    ok("в базе загрузок и актов ФИО нет", name not in stored and "Петров" not in stored
       and "Сидоров" not in stored and "Иванов" not in stored)
    journal = _json.dumps(audit, ensure_ascii=False) + _json.dumps(calls, ensure_ascii=False)
    ok("в журнал ФИО не попадает", "Иванов" not in journal and "Сидоров" not in journal and "Петров" not in journal)
    ok("в журнал не попадают распознанные значения", "QY50K5D" not in journal and "LXGCPA" not in journal)
    ok("в журнале есть счётчик отброшенного", any('"dropped_pd"' in (r["detail"] or "") for r in audit))


# ------------------------------------------------------------------ 7. доступ и лимит

def check_access(aid):
    print("7. Чужой акт и гостевой лимит")
    st, a = call("GET", f"/act/{aid}")
    ok("свой акт открывается", st == 200 and a["id"] == aid, st)
    saved = dict(COOKIES)
    COOKIES.clear()
    st, a = call("GET", f"/act/{aid}")
    ok("чужой гость — 404", st == 404, (st, a))
    st, _b, _h = call("GET", f"/act/{aid}.pdf", raw=True)
    ok("чужой PDF — 404", st == 404, st)
    st, _b, _h = call("GET", f"/act/{aid}.docx", raw=True)
    ok("чужой DOCX — 404", st == 404, st)
    # администратор видит любой
    now = datetime.now().isoformat(timespec="seconds")
    token = secrets.token_urlsafe(32)
    with db.tx() as con:
        cur = con.execute("INSERT INTO users (login, full_name, role, branch, password_hash, salt, status, created_at,"
                          " approved_by, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          ("act_test_admin", "Test Admin", "админ", "тест", secrets.token_hex(32),
                           secrets.token_hex(16), "активен", now, "test", now))
        con.execute("INSERT INTO sessions (token, user_id, created_at, expires_at, ip, user_agent) VALUES (?,?,?,?,?,?)",
                    (token, cur.lastrowid, now, (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds"),
                     "127.0.0.1", "test_act"))
    HEADERS.append((b"authorization", f"Bearer {token}".encode()))
    st, a = call("GET", f"/act/{aid}")
    ok("администратор открывает любой акт", st == 200, st)
    st, s = call("PUT", "/act/settings", {"settings": {"adj_pct": {"moderate": 25}}, "note": "тест"})
    ok("администратор меняет поправки", st == 200 and s["settings"]["adj_pct"]["moderate"] == 25, (st, s))
    st, s = call("PUT", "/act/settings", {"settings": {"adj_pct": {"low": -5}}})
    ok("кривые настройки — 422", st == 422, (st, s))
    HEADERS.clear()
    st, s = call("PUT", "/act/settings", {"settings": {"adj_pct": {"moderate": 30}}})
    ok("гость настройки не меняет", st in (401, 403), st)
    st, s = call("GET", "/act/settings")
    ok("настройки читаются", st == 200 and s["settings"]["adj_pct"]["moderate"] == 25, (st, s))
    with db.tx() as con:
        con.execute("DELETE FROM act_settings")
    COOKIES.clear()
    COOKIES.update(saved)

    guest.reset()
    old = guest.LIMITS["analysis"]
    guest.LIMITS["analysis"] = 2
    try:
        codes = [call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT})[0]
                 for _ in range(3)]
    finally:
        guest.LIMITS["analysis"] = old
        guest.reset()
    ok("гостевой лимит на акты срабатывает (429)", codes == [200, 200, 429], codes)


# ------------------------------------------------------------------ 8. Word и PDF

def pdf_text(doc) -> str:
    """Текст PDF: извлечение отдаёт пробел как NBSP, а дефис как мягкий перенос — приводим к обычным."""
    raw = "".join(p.get_text() for p in doc).replace(" ", " ").replace("­", "-")
    return re.sub(r"\s+", " ", raw)


RU_TITLES = ["Объект и идентификация", "Результаты осмотра", "Стоимость и страховая сумма",
             "Риск-факторы и франшиза", "Заключение и рекомендация"]


def check_files(aid):
    print("8. Word и PDF")
    st, blob, h = call("GET", f"/act/{aid}.docx", raw=True)
    ok("DOCX отдаётся", st == 200 and "wordprocessingml" in h.get("content-type", ""), (st, h))
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = set(z.namelist())
        xml = z.read("word/document.xml").decode("utf-8")
        minidom.parseString(xml.encode("utf-8"))
        good = {"[Content_Types].xml", "word/document.xml", "word/styles.xml"} <= names
        for n in names:
            if n.endswith(".xml") or n.endswith(".rels"):
                minidom.parseString(z.read(n))
    except Exception as e:
        good, xml = False, str(e)
    ok("DOCX — zip с корректным XML", good, xml[:200])
    plain = re.sub(r"<[^>]+>", "", xml)
    ok("в DOCX пять разделов", all(f"{i}. {tt}" in plain for i, tt in enumerate(RU_TITLES, 1)))
    ok("в DOCX строка о подтверждении андеррайтером",
       "Акт сформирован ИИ-сюрвейером, подлежит подтверждению андеррайтером" in plain)
    ok("в DOCX шапка", "СЮРВЕЙЕРСКИЙ АКТ ПРЕДСТРАХОВОГО ОСМОТРА" in plain)
    st, blob, h = call("GET", f"/act/{aid}.pdf", raw=True)
    ok("PDF отдаётся", st == 200 and h.get("content-type") == "application/pdf", (st, h))
    try:
        doc = pymupdf.open(stream=blob, filetype="pdf")
        text = pdf_text(doc)
        pages = doc.page_count
    except Exception as e:
        text, pages = str(e), 0
    ok("PDF открывается pymupdf", pages >= 1, text[:200])
    ok("в PDF пять разделов", all(f"{i}. {tt}" in text for i, tt in enumerate(RU_TITLES, 1)), text[:300])
    ok("в PDF строка о подтверждении андеррайтером",
       "Акт сформирован ИИ-сюрвейером, подлежит подтверждению андеррайтером" in text)
    st, blob, h = call("GET", f"/act/{aid}.pdf", params={"lang": "uz"}, raw=True)
    doc = pymupdf.open(stream=blob, filetype="pdf")
    text = pdf_text(doc)
    ok("PDF на узбекском", "Koʻzdan kechirish natijalari" in text or "Ko'zdan kechirish natijalari" in text,
       text[:300])


# ------------------------------------------------------------------ 9. три языка

def check_langs(aid):
    print("9. Три языка")
    want = {"ru": RU_TITLES,
            "uz": ["Obyekt va uni identifikatsiya qilish", "Koʻzdan kechirish natijalari", "Qiymat va sugʻurta summasi",
                   "Xavf omillari va franshiza", "Xulosa va tavsiya"],
            "en": ["Object and identification", "Inspection results", "Value and sum insured",
                   "Risk factors and deductible", "Conclusion and recommendation"]}
    for lang, titles in want.items():
        st, a = call("GET", f"/act/{aid}", params={"lang": lang})
        ok(f"{lang}: заголовки разделов", st == 200 and [s["title"] for s in a["sections"]] == titles,
           [s.get("title") for s in a.get("sections") or []])
        if lang != "ru":
            labels = [a["title"], a["footer"]] + [r["label"] for s in a["sections"] for r in s["rows"]] + \
                     [li["title"] for s in a["sections"] for li in s.get("lists") or []] + \
                     [f["text"] for f in a["risk"]["factors"]] + a["decision"]["checks"] + [a["franchise"]["text"]] + \
                     a["rate"]["how"] + [a["value"]["text"], a["risk"]["rule"]] + \
                     [p for s in a["sections"] for p in s["paragraphs"]]
            cyr = [x for x in labels if re.search(r"[А-Яа-яЁё]", x or "")]
            ok(f"{lang}: в подписях и выводах нет кириллицы", not cyr, cyr[:5])
    st, a = call("POST", "/act/make", {"lang": "en", "must": CRANE_MUST, "optional": CRANE_OPT})
    ok("en: сумма в формате UZS", "2,945,000,000 UZS" in all_text(a), all_text(a)[:300])


# ------------------------------------------------------------------ 10. движок: чистые функции

def check_engine():
    print("10. Лёгкий движок (чистые функции)")
    today = date(2026, 9, 29)
    r = ae.risk_level({"inspected": True, "damages": ["вмятина"], "location": "construction", "losses_count": 3,
                       "documents": False, "today": today})
    ok("все четыре признака «повышают» — высокий", r["level"] == "high" and r["up"] == 4, r)
    r = ae.risk_level({"inspected": False, "documents": True, "today": today})
    ok("мало известного — умеренный", r["level"] == "moderate", r)
    r = ae.risk_level({"inspected": True, "year": 2026, "location": "guarded", "losses_count": 0,
                       "documents": True, "today": today})
    ok("всё снижает — низкий", r["level"] == "low" and r["down"] == 4, r)
    r = ae.risk_level({"inspected": True, "year": 2015, "location": "open_area", "guard": True,
                       "losses_count": 1, "documents": True, "today": today})
    ok("открытая площадка под охраной и один убыток — не влияют", r["up"] == 0 and r["down"] == 1, r)
    r = ae.risk_level({"inspected": True, "documents": True, "today": today})
    ok("год неизвестен — не пишем «не новый»", r["factors"][0]["code"] == "f_cond_no_year", r["factors"][0])
    d = ae.discrepancies([{"key": "curb_mass", "value": "36 170 кг", "source": "plate"},
                          {"key": "curb_mass", "value": "36,17 т", "source": "document"}])
    ok("масса в тоннах и килограммах — одно и то же", not d, d)
    d = ae.discrepancies([{"key": "serial_no", "value": "LXGCPA393TA006921", "source": "plate"},
                          {"key": "serial_no", "value": "LXGCPA393TA006927", "source": "document"}])
    ok("номер отличается одним знаком — расхождение", len(d) == 1 and d[0]["priority"] == "document", d)
    d = ae.discrepancies([{"key": "year", "value": "2025", "source": "document"}], {"year": 2026})
    ok("год во вводе и в документе — расхождение", len(d) == 1 and d[0]["key"] == "year", d)
    d = ae.discrepancies([{"key": "model", "value": "QY50K5D", "source": "document"},
                          {"key": "model", "value": "QY50K", "source": "marking"}])
    ok("модель на стреле и в документе — расхождение", len(d) == 1, d)
    ok("ракурсы здания", ae.required_views("property") == ["facade", "roof", "interior", "electrical",
                                                           "fire_safety"])
    ok("группа по классу и типу", ae.object_group("3", "Спецтехника — автокран") == "special"
       and ae.object_group("8", "Склад") == "property" and ae.object_group("7") == "cargo")
    ok("ошибки настроек ловятся", ae.check_settings({"level_rule": {"low_max_net": 3, "high_min_net": 1}}))
    ok("верные настройки проходят", not ae.check_settings({"adj_pct": {"moderate": 25}}))
    ok("число из «38,600 kg» и «2 945 000 000»", ae.to_number("38,600 kg") == 38600
       and ae.to_number("2 945 000 000") == 2945000000)


# ------------------------------------------------------------------ 11. связка текста моделью

def check_polish():
    print("11. Литературная связка моделью убрана: решение и списки — только из шаблонов")
    model_on(True)
    REPLY["text"] = "Объект осмотрен, выявлено 999 замечаний. Рекомендация: отказать."
    CALLS.clear()
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT, "polish": True})
    ok("polish в теле игнорируется: к модели не обращались", st == 200 and not CALLS, len(CALLS))
    ok("ни один раздел не переписан моделью",
       not any(s.get("polished") for s in a["sections"]) and "999" not in all_text(a))
    ok("решение — из шаблона", a["decision"]["text"] == act.t("d_" + a["decision"]["code"], "ru"), a["decision"])
    ok("функции polish больше нет", not hasattr(act, "polish"))
    model_on(False)


# ------------------------------------------------------------------ 12. хранение

def check_cleanup(sid, aid):
    print("12. Хранение: фото 24 часа, акт 7 дней")
    with db.tx() as con:
        up = db.rows(con, "SELECT created_at, expires_at, files_json FROM act_uploads WHERE id=?", sid)[0]
        ac = db.rows(con, "SELECT created_at, expires_at FROM acts WHERE id=?", aid)[0]
    h = (datetime.fromisoformat(up["expires_at"]) - datetime.fromisoformat(up["created_at"])).total_seconds() / 3600
    d = (datetime.fromisoformat(ac["expires_at"]) - datetime.fromisoformat(ac["created_at"])).days
    ok("фото — 24 часа", h == 24, h)
    ok("акт — 7 дней", d == 7, d)
    files = _json.loads(up["files_json"])
    ok("в базе у файла нет имени — только номер, формат и путь",
       all("name" not in f and "orig_name" not in f and f.get("index") and f.get("fmt") for f in files)
       and "front.jpg" not in up["files_json"], files[:1])
    ok("путь файла — через db.stored_path", all(f["path"] == db.stored_path(act.DIR / sid / Path(f["path"]).name)
                                                for f in files), files[:1])
    ok("файлы на диске", (act.DIR / sid).is_dir())
    with db.tx() as con:
        con.execute("UPDATE act_uploads SET expires_at='2020-01-01T00:00:00' WHERE id=?", (sid,))
        con.execute("UPDATE acts SET expires_at='2020-01-01T00:00:00' WHERE id=?", (aid,))
    removed = act.cleanup()
    ok("просроченное удаляется", removed >= 2, removed)
    ok("папка с фото удалена", not (act.DIR / sid).exists())
    st, a = call("GET", f"/act/{aid}")
    ok("просроченный акт не открывается", st == 404, st)


# ------------------------------------------------------------------ 13–20. замечания контролёра 29.09.2026

def set_limits(**kw):
    """Версия настроек акта с другими пределами (как правка администратора), без API."""
    with db.tx() as con:
        con.execute("INSERT INTO act_settings (created_at, created_by, settings_json, calibrated, note) "
                    "VALUES (?,?,?,?,?)", (db.now(), "тест", _json.dumps({"limits": kw}), 0, "тест"))


def clear_settings():
    with db.tx() as con:
        con.execute("DELETE FROM act_settings")


def png_bomb(w=14000, h=14000) -> bytes:
    """Настоящая «бомба»: PNG 14000×14000 оттенков серого из нулей — сотни килобайт в файле, ~200 МБ в памяти."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    comp = zlib.compressobj(9)
    row = b"\x00" * (w + 1)
    parts = [comp.compress(row * 500) for _ in range(h // 500)] + [comp.flush()]
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", b"".join(parts)) + chunk(b"IEND", b""))


def jpeg_claiming(w, h) -> bytes:
    """JPEG, у которого в SOF0 записаны огромные размеры (данные кадра — мусор)."""
    import struct
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, 3) + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof + b"\xff\xda\x00\x02" + b"\x00" * 64 + b"\xff\xd9"


def pdf_pages(n) -> bytes:
    doc = pymupdf.open()
    for _ in range(n):
        doc.new_page()
    return doc.tobytes()


def noise(w, h, kind="png") -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, os.urandom(w * h * 3), False)
    return pix.tobytes("jpg" if kind == "jpg" else "png")


def check_bomb():
    print("13. Картинка-бомба и PDF: размеры из заголовка, без раскрытия")
    model_on(True)
    REPLY["text"] = CRANE_REPLY
    act.reset_limits()
    bomb = png_bomb()
    opened = []
    orig_pix = pymupdf.Pixmap

    class spy(orig_pix):                     # подкласс: внутренние проверки isinstance в pymupdf не ломаются
        def __init__(self, *a, **kw):
            opened.append(a[:1] if a and isinstance(a[0], (bytes, bytearray)) else "other")
            super().__init__(*a, **kw)
    ok("бомба маленькая на диске", len(bomb) < 1024 * 1024, len(bomb))
    ok("размеры PNG читаются из IHDR", act.image_size(bomb, "png") == (14000, 14000))
    ok("размеры JPEG — из SOF", act.image_size(image(kind="jpg", w=321, h=123), "jpg") == (321, 123))
    ok("размеры JPEG с мусором в SOF", act.image_size(jpeg_claiming(12000, 9000), "jpg") == (12000, 9000))
    ok("битый заголовок — размеров нет", act.image_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "png") is None
       and act.image_size(b"\xff\xd8\xff\xda\x00\x02", "jpg") is None)
    pymupdf.Pixmap = spy
    try:
        CALLS.clear()
        st, b = upload([("bomb.png", "image/png", bomb), ("big.jpg", "image/jpeg", jpeg_claiming(12000, 9000)),
                        ("broken.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),
                        ("ok.png", "image/png", image())], {"lang": "ru"})
    finally:
        pymupdf.Pixmap = orig_pix
    rej = {r["index"]: r["error"] for r in b.get("rejected") or []}
    ok("бомба 14000×14000 отклонена с понятным сообщением",
       st == 200 and "14000×14000" in rej.get(1, "") and "50 Мп" in rej.get(1, ""), rej)
    ok("JPEG 12000×9000 (108 Мп) отклонён", "Мп" in rej.get(2, ""), rej)
    ok("размеры не прочитались — отклонено", "размеры" in rej.get(3, ""), rej)
    ok("нормальный снимок принят (index 4)", [f["index"] for f in b["files"]] == [4], b.get("files"))
    ok("отклонённые картинки не раскрывались (Pixmap только для принятой)",
       len([x for x in opened if x != "other"]) == 1, len(opened))
    ok("к модели ушёл один файл", len(CALLS) == 1 and len(CALLS[0]["files"]) == 1)
    st, b = upload([("many.pdf", "application/pdf", pdf_pages(11)), ("one.pdf", "application/pdf", pdf_pages(1))],
                   {"lang": "ru"})
    rej = {r["index"]: r["error"] for r in b.get("rejected") or []}
    ok("PDF больше 10 страниц отклонён", "10 страниц" in rej.get(1, ""), rej)
    ok("PDF в 1 страницу принят и не растеризуется (уходит модели как PDF)",
       [f["index"] for f in b["files"]] == [2] and CALLS[-1]["files"][0]["mime"] == "application/pdf")
    set_limits(max_image_mp=0.05)             # 320×240 = 0,077 Мп — теперь больше предела
    try:
        st, b = upload([("ok.png", "image/png", image())], {"lang": "ru"})
        ok("предел мегапикселей — из настроек", st == 422 and "Мп" in b["rejected"][0]["error"], b)
    finally:
        clear_settings()


async def _asend(method, path, headers, payload, out):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
             "headers": headers, "client": ("203.0.113.9", 0), "server": ("test", 80)}
    res = {"status": None}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            res["status"] = msg["status"]
    await app(scope, receive, send)
    import time as _t
    out.append((path, res["status"], _t.monotonic()))


def multipart(files, fields=None):
    boundary = "----insonact"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in (fields or {}).items()]
    for name, mime, blob in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
                     f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(parts)


def check_threadpool():
    print("14. Распознавание не останавливает сервер; срок 25 с; одна попытка")
    import inspect
    import time as _t
    from app import act as act_mod
    eps = {}
    for r in act_mod.router.routes:
        eps[(r.path, tuple(sorted(r.methods)))] = r.endpoint
    heavy = [eps[("/act/photos", ("POST",))], eps[("/act/make", ("POST",))], eps[("/act/{aid}.docx", ("GET",))],
             eps[("/act/{aid}.pdf", ("GET",))], eps[("/act/{aid}/send", ("POST",))]]
    ok("обработчики фото, акта, выгрузки и отправки — обычные def (пул потоков)",
       not any(inspect.iscoroutinefunction(f) for f in heavy), [f.__name__ for f in heavy
                                                                if inspect.iscoroutinefunction(f)])
    # пока идёт медленное распознавание (1,5 с), другой запрос отвечает сразу
    model_on(True)
    REPLY["text"], REPLY["sleep"] = CRANE_REPLY, 1.5
    act.reset_limits()
    boundary, payload = multipart(CRANE_FILES[:1], {"lang": "ru"})
    up_hdrs = [(b"host", b"test"), (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
               (b"content-length", str(len(payload)).encode())] + _cookie_hdr()
    get_hdrs = [(b"host", b"test")] + _cookie_hdr()
    done = []

    async def both():
        import asyncio as _a
        t0 = _t.monotonic()
        up = _a.ensure_future(_asend("POST", "/act/photos", up_hdrs, payload, done))
        await _a.sleep(0.2)
        await _asend("GET", "/act/settings", get_hdrs, b"", done)
        await up
        return t0
    try:
        t0 = asyncio.run(both())
    finally:
        REPLY["sleep"] = 0
    order = [p for p, _s, _ in done]
    ok("GET /act/settings ответил раньше, чем закончилось распознавание",
       order == ["/act/settings", "/act/photos"] and done[0][2] - t0 < 1.2, [(p, round(x - t0, 2)) for p, _s, x in done])
    ok("загрузка при этом прошла", done[-1][1] == 200, done)
    # общий срок: модель «висит» дольше срока — ai=false, честная причина
    REPLY["sleep"] = 1.5
    try:
        t1 = _t.monotonic()
        rec = act.recognize([{"blob": image(), "fmt": "png"}], "ru", {"ai_deadline_sec": 0.5})
        spent = _t.monotonic() - t1
    finally:
        REPLY["sleep"] = 0
    ok("не уложились в срок — ai=false и сообщение", rec["ok"] is False and "не ответила" in rec["reason"], rec)
    ok("ответ не ждёт модель дольше срока", spent < 1.2, spent)
    # сервер-подобный путь: модель падает — фото сохранены, акт формируется
    def boom(*a, **kw):
        raise RuntimeError("сеть")
    fake_raw = llm.chat_raw
    llm.chat_raw = boom
    try:
        st, b = upload(CRANE_FILES[:2], {"lang": "ru"})
    finally:
        llm.chat_raw = fake_raw
    ok("ошибка модели — фото сохранены, ai=false", st == 200 and b["ai"] is False and b["session"]
       and (act.DIR / b["session"]).is_dir(), b)
    st, a = call("POST", "/act/make", {"session": b["session"], "lang": "ru", "must": CRANE_MUST,
                                       "optional": CRANE_OPT})
    ok("акт после сбоя модели формируется", st == 200 and a["inspection"]["done"] is False, st)
    # llm.chat_raw: retries=0 — ровно один запрос к сети, таймаут передан
    posts = []

    def fake_post(url, body, headers, timeout=None):
        posts.append(timeout)
        raise TimeoutError("медленно")
    old = (llm._post, llm.enabled, llm.provider)
    llm._post, llm.enabled, llm.provider = fake_post, (lambda: True), (lambda: "openai")
    try:
        res = ORIG["chat_raw"]("тест", [{"role": "user", "content": "x"}], timeout=20, retries=0)
    finally:
        llm._post, llm.enabled, llm.provider = old
    ok("llm.chat_raw(retries=0): одна попытка, таймаут 20 с", posts == [20] and res["text"] is None, posts)


def check_body_limit():
    print("15. Размер тела /act/photos — по Content-Length до чтения")
    model_on(True)
    REPLY["text"] = CRANE_REPLY
    CALLS.clear()
    before = set(p.name for p in act.DIR.iterdir()) if act.DIR.exists() else set()
    boundary, payload = multipart(CRANE_FILES[:1], {"lang": "ru"})
    read = []

    async def run(headers):
        out = {}

        async def receive():
            read.append(1)
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/act/photos", "raw_path": b"/act/photos", "root_path": "",
                 "query_string": b"", "headers": headers, "client": ("203.0.113.9", 0), "server": ("test", 80)}
        await app(scope, receive, send)
        return out.get("status")
    ctype = (b"content-type", f"multipart/form-data; boundary={boundary}".encode())
    st = asyncio.run(run([(b"host", b"test"), ctype, (b"content-length", str(act.MAX_BODY + 1).encode())]
                         + _cookie_hdr()))
    ok("больше 10 × 15 МБ + запас — 413", st == 413, st)
    ok("тело при этом не читалось", not read, len(read))
    st = asyncio.run(run([(b"host", b"test"), ctype] + _cookie_hdr()))
    ok("без Content-Length — 411", st == 411, st)
    after = set(p.name for p in act.DIR.iterdir()) if act.DIR.exists() else set()
    ok("ни файлов, ни обращений к модели", after == before and not CALLS)
    st = asyncio.run(run([(b"host", b"test"), ctype, (b"content-length", str(len(payload)).encode())]
                         + _cookie_hdr()))
    ok("обычный размер проходит", st == 200, st)


def check_limits():
    print("16. Лимиты: фото гостя по числу файлов, распознавания на сервер")
    model_on(True)
    REPLY["text"] = CRANE_REPLY
    act.reset_limits()
    guest.reset()
    set_limits(guest_photos_per_hour=3)
    try:
        codes = [upload(CRANE_FILES[:2], {"lang": "ru"})[0], upload(CRANE_FILES[:2], {"lang": "ru"}),
                 upload(CRANE_FILES[:1], {"lang": "ru"})[0]]
    finally:
        clear_settings()
        act.reset_limits()
    st2, b2 = codes[1]
    ok("2 фото + 2 фото при пределе 3 — второй запрос 429, третий (1 фото) проходит",
       [codes[0], st2, codes[2]] == [200, 429, 200], [codes[0], st2, codes[2]])
    ok("429 с понятным сообщением и Retry-After", "фото в час" in b2.get("detail", "") and b2.get("limit") == 3
       and b2.get("retry_after_sec"), b2)
    ok("по умолчанию 60 фото в час", ae.DEFAULT_SETTINGS["limits"]["guest_photos_per_hour"] == 60
       and ae.DEFAULT_SETTINGS["limits"]["ai_calls_per_hour"] == 120)
    # guard больше не считает /act/photos запросами (иначе 30 запросов в час перекрыли бы счёт по фото)
    from app import guard
    ok("guard не считает /act/photos запросами", ("POST", "/act/photos") not in guard.GUEST_BUCKET)
    set_limits(ai_calls_per_hour=1)
    CALLS.clear()
    try:
        _s1, b1 = upload(CRANE_FILES[:1], {"lang": "ru"})
        _s2, b2 = upload(CRANE_FILES[:1], {"lang": "ru"})
    finally:
        clear_settings()
        act.reset_limits()
    ok("общий предел распознаваний: второй раз ai=false с честным сообщением",
       b1["ai"] is True and b2["ai"] is False and "лимит" in b2["message"] and len(CALLS) == 1, (b2["message"],
                                                                                                  len(CALLS)))
    ok("фото при этом сохранены", _s2 == 200 and b2["session"])
    ok("кривые пределы в настройках ловятся",
       ae.check_settings({"limits": {"max_image_mp": 0}}) and ae.check_settings({"limits": {"x": 1}})
       and not ae.check_settings({"limits": {"max_image_mp": 40}}))


def check_budget():
    print("17. Вложения в модель — не больше 10 МБ (настройка), о непрочитанном — честно")
    model_on(True)
    REPLY["text"] = _json.dumps({"files": [], "fields": [], "damages": []})
    act.reset_limits()
    big = [noise(1200, 1200, "png") for _ in range(3)]
    set_limits(ai_max_mb=1)
    CALLS.clear()
    try:
        st, b = upload([("a.png", "image/png", big[0]), ("b.png", "image/png", big[1]),
                        ("c.png", "image/png", big[2]), ("d.pdf", "application/pdf", pdf_pages(1))], {"lang": "ru"})
    finally:
        clear_settings()
    sent = CALLS[0]["files"] if CALLS else []
    total = sum(len(f["data"]) for f in sent)
    ok("суммарно в модель не больше предела", st == 200 and 0 < total <= 1024 * 1024, total)
    ok("PDF (документ) — в приоритете", any(f["mime"] == "application/pdf" for f in sent))
    ok("непрочитанные перечислены номерами", b.get("not_sent") and b["notes"]
       and all(str(n) in b["notes"][0] for n in b["not_sent"]), (b.get("not_sent"), b.get("notes")))
    ok("у непрочитанных read_by_ai = false",
       all(not f["read_by_ai"] for f in b["files"] if f["index"] in b["not_sent"]))
    ok("по умолчанию предел 10 МБ", ae.DEFAULT_SETTINGS["limits"]["ai_max_mb"] == 10)
    # сильнее пережать: 4 шума 2000×2000 не влезают в 10 МБ при обычном сжатии, но влезают после второго шага
    many = [{"blob": noise(2000, 2000, "jpg"), "fmt": "jpg"} for _ in range(4)]
    first = sum(len(act._for_model(f["blob"], "jpg")[0]) for f in many)
    payload, sent_i, left = act.pick_for_model(many, 10 * 1024 * 1024)
    ok("не поместились — пережаты сильнее, а не выброшены" if first > 10 * 1024 * 1024 else
       "поместились при обычном сжатии", len(sent_i) == 4 and not left
       and sum(len(p["data"]) for p in payload) <= 10 * 1024 * 1024, (first, [len(p["data"]) for p in payload]))


def check_sources():
    print("18. Источник значения нельзя подменить")
    model_on(True)
    REPLY["text"] = CRANE_REPLY
    act.reset_limits()
    st, b = upload(CRANE_FILES, {"lang": "ru", "product_code": "0318"})
    sid = b["session"]
    fake = [{"key": "serial_no", "value": "FAKE0000000000001", "source": "document", "file": "f4"}]
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT,
                                       "recognized": fake})
    rec = {(r["key"], r["value"]): r for r in a["recognized"]}
    ok("без загрузки «из документа» превращается во «введено сотрудником»",
       rec[("serial_no", "FAKE0000000000001")]["source"] == "input", a["recognized"])
    s1 = {r["label"]: r for r in a["sections"][0]["rows"]}
    ok("в разделе 1 — «введено сотрудником»", "введено сотрудником" in (s1["Заводской (серийный) номер"]["note"]
                                                                         or ""), s1["Заводской (серийный) номер"])
    edited = [dict(r) for r in b["recognized"]]
    for r in edited:
        if r["key"] == "serial_no" and r["source"] == "document":
            r["value"] = "LXGCPA393TA000000"            # сотрудник исправил значение, источник оставил
    st, a = call("POST", "/act/make", {"session": sid, "lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT,
                                       "recognized": edited + fake})
    rec = {(r["key"], r["value"]): r["source"] for r in a["recognized"]}
    ok("изменённое значение — input", rec.get(("serial_no", "LXGCPA393TA000000")) == "input", rec)
    ok("чужое значение с source=document в живой сессии — input",
       rec.get(("serial_no", "FAKE0000000000001")) == "input", rec)
    ok("неизменённые значения сохраняют источник", rec.get(("serial_no", "LXGCPA393TA006921")) == "plate"
       and rec.get(("curb_mass", "38 600 кг")) == "document", rec)
    ok("расхождение по номеру не выдумано из подмены",
       not any(d["key"] == "serial_no" and any("document" in g["sources"] and "FAKE" in g["value"]
                                               for g in d["values"]) for d in a["discrepancies"]), a["discrepancies"])
    with db.tx() as con:
        row = db.rows(con, "SELECT detail FROM audit WHERE action='акт сформирован' ORDER BY id DESC LIMIT 1")
    ok("в журнале — счётчик понижённых источников", '"sources_downgraded": 2' in row[0]["detail"], row)
    saved = dict(COOKIES)
    COOKIES.clear()
    st, a = call("POST", "/act/make", {"session": sid, "lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT,
                                       "recognized": b["recognized"]})
    COOKIES.clear()
    COOKIES.update(saved)
    ok("чужая сессия — все источники input", st == 200 and all(r["source"] == "input" for r in a["recognized"]),
       {r["source"] for r in a["recognized"]})


def init_data(tg_id: int, token: str, auth_date=None) -> str:
    import hashlib
    import hmac
    import time as _t
    from urllib.parse import urlencode as _ue
    pairs = [("auth_date", str(int(auth_date or _t.time()))), ("query_id", "AAHtest"),
             ("user", _json.dumps({"id": tg_id, "first_name": "Test"}, separators=(",", ":")))]
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return _ue(pairs + [("hash", h)])


def check_send(aid):
    print("19. Отправка акта ботом: POST /act/{id}/send")
    from app import telegram, tgbot
    token = "123456:TEST-token-не-для-журнала"
    sent = []
    reply = {"ok": True, "result": {"message_id": 5}}

    def fake_file(method, fields, field, filename, blob, mime):
        sent.append({"method": method, "chat_id": fields.get("chat_id"), "field": field, "filename": filename,
                     "head": blob[:4], "mime": mime, "caption": fields.get("caption")})
        return dict(reply)
    old = (tgbot.bot_token, telegram.bot_token, tgbot._deliver_file, tgbot._deliver)
    tgbot.bot_token = telegram.bot_token = lambda: token
    tgbot._deliver_file = fake_file
    tgbot._deliver = no_network
    act.reset_limits()
    try:
        good = init_data(777000111, token)
        st, r = call("POST", f"/act/{aid}/send", {"format": "pdf"})
        ok("гость без Telegram — 409 с понятным сообщением",
           st == 409 and r.get("code") == "no_telegram" and "Telegram" in r["detail"]
           and "компьютере" in r["detail"], (st, r))
        st, r = call("POST", f"/act/{aid}/send", {"format": "pdf", "initData": init_data(777000111, "чужой:токен")})
        ok("поддельный initData — 403", st == 403 and r.get("code") == "bad_init_data", (st, r))
        st, r = call("POST", f"/act/{aid}/send", {"format": "xls", "initData": good})
        ok("неизвестный формат — 422", st == 422, (st, r))
        st, r = call("POST", f"/act/{aid}/send", {"format": "pdf", "lang": "uz", "initData": good})
        ok("PDF отправлен: 200 и sendDocument этому пользователю",
           st == 200 and r.get("sent") and sent and sent[-1]["method"] == "sendDocument"
           and sent[-1]["chat_id"] == "777000111" and sent[-1]["head"] == b"%PDF"
           and sent[-1]["filename"].endswith(".pdf"), (st, r, sent[-1:]))
        ok("язык файла — из запроса", r.get("lang") == "uz" and "Syurveyer" in (sent[-1]["caption"] or ""), r)
        st, r = call("POST", f"/act/{aid}/send", {"format": "docx", "initData": good})
        ok("DOCX отправлен документом", st == 200 and sent[-1]["head"][:2] == b"PK"
           and sent[-1]["filename"].endswith(".docx") and sent[-1]["method"] == "sendDocument", (st, sent[-1:]))
        saved = dict(COOKIES)
        COOKIES.clear()
        n_before = len(sent)
        st, r = call("POST", f"/act/{aid}/send", {"format": "pdf", "initData": good})
        COOKIES.clear()
        COOKIES.update(saved)
        ok("не владелец — 404 и ничего не отправлено", st == 404 and len(sent) == n_before, (st, r))
        reply.update(ok=False, description="Forbidden: bot can't initiate conversation with a user")
        st, r = call("POST", f"/act/{aid}/send", {"format": "pdf", "initData": good})
        ok("бот не может написать первым — 502 и подсказка нажать Start",
           st == 502 and r.get("code") == "start_bot" and "Start" in r["detail"], (st, r))
        reply.clear()
        reply.update(ok=True, result={"message_id": 6})
        codes = [call("POST", f"/act/{aid}/send", {"format": "pdf", "initData": good})[0] for _ in range(8)]
        ok("лимит: 10 отправок в час, 11-я — 429", codes[:7] == [200] * 7 and codes[7] == 429, codes)
        tgbot.bot_token = telegram.bot_token = lambda: ""
        act.reset_limits()
        st, r = call("POST", f"/act/{aid}/send", {"format": "pdf", "initData": good})
        ok("бот не подключён — 503", st == 503 and r.get("code") == "bot_off", (st, r))
        with db.tx() as con:
            dump = _json.dumps(db.rows(con, "SELECT * FROM audit WHERE action LIKE 'акт%'"), ensure_ascii=False) + \
                _json.dumps(db.rows(con, "SELECT * FROM tg_messages"), ensure_ascii=False)
        ok("токен бота не попал ни в журнал, ни в журнал бота", "TEST-token" not in dump)
    finally:
        tgbot.bot_token, telegram.bot_token, tgbot._deliver_file, tgbot._deliver = old
        act.reset_limits()


def check_misc():
    print("20. Мелочи: ФИО и номера, регион, несколько классов, папки-сироты")
    pl = act.pd_like
    ok("изготовитель — название компании целиком: не ФИО",
       not pl("manufacturer", "Xuzhou Construction Machinery Group Co., Ltd.")
       and not pl("manufacturer", "XUZHOU CONSTRICTION MACHINERY GROUP IMPORT & EXPORT CO., LTD (XCMG)"))
    ok("компания + ФИО вне названия — ФИО", pl("manufacturer", "Xuzhou Machinery Group Co., Ltd., Ivan Petrov")
       and pl("manufacturer", "ООО «Техника», директор Иванов Иван Иванович")
       and pl("brand", "Иванов И. И. Group"))
    ok("9 цифр без букв в номере — отброшено (возможный ИНН/телефон)", pl("serial_no", "123456789")
       and pl("engine_no", "301 234 567".replace(" ", "")))
    ok("номер с буквами — проходит", not pl("engine_no", "D123456789") and not pl("serial_no", "A123456789"))
    ok("лист параметров заказчика: «Паспорт № 20102600523» и «D9264002842» проходят",
       not pl("serial_no", "Паспорт № 20102600523") and not pl("engine_no", "D9264002842")
       and not pl("engine_model", "SC9DF340Q6") and not pl("serial_no", "LXGCPA393TA006921"))
    # регион кодом и названием
    st, a = call("POST", "/act/make", {"lang": "ru", "must": dict(CRANE_MUST, region="tashkent_region"),
                                       "optional": CRANE_OPT})
    reg = {r["label"]: r["value"] for r in a["sections"][0]["rows"]}.get("Регион")
    ok("регион кодом печатается по-русски", reg == "Ташкентская область", reg)
    st, u = call("GET", f"/act/{a['id']}", params={"lang": "uz"})
    reg_uz = [r["value"] for r in u["sections"][0]["rows"]][-1] if u.get("sections") else None
    ok("тот же акт на узбекском — регион по-узбекски",
       any(r["value"] == "Toshkent viloyati" for r in u["sections"][0]["rows"]), reg_uz)
    st, a = call("POST", "/act/make", {"lang": "en", "must": dict(CRANE_MUST, region="Ташкентская область"),
                                       "optional": CRANE_OPT})
    ok("каноническое название → перевод на язык акта",
       any(r["value"] == "Tashkent region" for r in a["sections"][0]["rows"]))
    st, a = call("POST", "/act/make", {"lang": "en", "must": dict(CRANE_MUST, region="Чирчик, промзона"),
                                       "optional": CRANE_OPT})
    ok("незнакомый регион — как ввели", any(r["value"] == "Чирчик, промзона" for r in a["sections"][0]["rows"]))
    # продукт с несколькими классами
    with db.tx() as con:
        multi = db.rows(con, "SELECT pc.product_code AS code FROM product_classes pc JOIN products p "
                             "ON p.code = pc.product_code WHERE p.pricing_mode NOT IN ('по программе') "
                             "GROUP BY pc.product_code HAVING COUNT(DISTINCT pc.class_code) > 1 LIMIT 1")
    if multi:
        st, a = call("POST", "/act/make", {"lang": "ru", "must": dict(CRANE_MUST, product_code=multi[0]["code"]),
                                           "optional": CRANE_OPT})
        text = all_text(a) if st == 200 else str(a)
        ok(f"продукт {multi[0]['code']} с несколькими классами — пометка в акте",
           st == 200 and a["rate"]["multi_class"] and "нескольким классам" in text
           and "разбор по частям отложен" in text, text[:300])
    else:
        ok("в справочнике нашёлся продукт с несколькими классами", False)
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT})
    ok("у продукта с одним классом пометки нет", not a["rate"]["multi_class"]
       and "нескольким классам" not in all_text(a))
    # папка-сирота: запись в базу упала — папки нет
    model_on(False)
    act.reset_limits()
    before = set(p.name for p in act.DIR.iterdir()) if act.DIR.exists() else set()
    orig_cleanup = act.cleanup

    def broken(con=None):
        raise RuntimeError("база недоступна")
    act.cleanup = broken
    try:
        try:
            st, _b = upload(CRANE_FILES[:1], {"lang": "ru"})
        except Exception as e:                     # ASGI-приложение пробрасывает исключение после 500
            st = type(e).__name__
    finally:
        act.cleanup = orig_cleanup
    after = set(p.name for p in act.DIR.iterdir()) if act.DIR.exists() else set()
    ok("запись в базу упала — папка с фото удалена", after == before, (st, after - before))
    old_dir = act.DIR / "deadbeefdeadbeefdeadbeef"
    new_dir = act.DIR / "cafecafecafecafecafecafe"
    old_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / "f1.png").write_bytes(image())
    past = datetime.now().timestamp() - 3 * 3600
    os.utime(old_dir, (past, past))
    act.cleanup()
    ok("очистка: старая папка без записи удалена, свежая оставлена", not old_dir.exists() and new_dir.exists())
    shutil.rmtree(new_dir, ignore_errors=True)


# ------------------------------------------------------------------ 21–27. дополнения 29.09.2026

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CONTRACT = Path(__file__).resolve().parent.parent / "sandbox" / "flow150_contract.docx"
WH_MUST = {"product_code": "0808", "sum_insured": 1_000_000_000, "object_value": 1_200_000_000,
           "region": "Ташкент"}
WH_OPT = {"object_kind": "warehouse", "protection": "alarm", "losses_3y": {"count": 0, "small_count": 0}}


def fresh():
    guest.reset()
    act.reset_limits()


def docx_bytes(lines, extra_parts=None) -> bytes:
    """Минимальный DOCX: абзацы из строк (и лишние части архива, если нужны)."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{ln}</w:t></w:r></w:p>" for ln in lines)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("word/document.xml", xml)
        for name, data in (extra_parts or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def docx_bomb(mb=60) -> bytes:
    """DOCX с частью из нулей: в файле — десятки килобайт, после распаковки — mb мегабайт."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document/>')
        z.writestr("word/media/zero.bin", b"\x00" * (mb * 1024 * 1024))
    return buf.getvalue()


def ra_same(con, must, optional):
    """Расчёт risk_analytics напрямую — тем же быстрым режимом, что и акт."""
    from app import risk_analytics as ra
    m, o, a = ra.apply_defaults(con, must, optional)
    return ra.analyze(con, m, o, assumptions=a)


# сценарий акта ← сценарий risk_analytics (там названия EML и PML переставлены, модуль не меняем)
MAP_RA = {"PML": "EML", "EML": "PML", "MFL": "MFL"}
SCEN_REPORT = {}                          # цифры сценариев для отчёта (печатаются в конце)


def order_ok(sc: dict) -> bool:
    return sc["pml"]["amount"] <= sc["eml"]["amount"] <= sc["mfl"]["amount"]


def check_scenarios():
    print("21. Сценарии PML / EML / MFL — сверка с risk_analytics (порядок заказчика PML ≤ EML ≤ MFL)")
    fresh()
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, object_kind="truck_crane")})
    sc = a.get("scenarios") or {}
    ok("автокран: блок сценариев есть", st == 200 and sc.get("available") and sc.get("rule") == "vehicle", sc)
    with db.tx() as con:
        an = ra_same(con, {"class_code": "3", "product_code": "0318", "object_type": CRANE_TYPE,
                           "sum_insured": CRANE_MUST["sum_insured"], "object_value": CRANE_MUST["object_value"],
                           "region": CRANE_MUST["region"], "vehicle_type": "special"},
                     {"losses_3y": {"count": 0, "amount": 0, "small_count": 0}})
    # порядок заказчика PML ≤ EML ≤ MFL: PML акта = EML модуля («защита сработала»), EML акта = PML модуля
    for s in ("PML", "EML", "MFL"):
        src = MAP_RA[s]
        ok(f"автокран: {s} акта = {src} risk_analytics", sc[s.lower()]["amount"] == round(an["scenarios"][src]["amount"])
           and sc[s.lower()]["pct"] == round(an["scenarios"][src]["amount"] / CRANE_MUST["sum_insured"] * 100, 1)
           and sc[s.lower()]["source_scenario"] == src, (sc.get(s.lower()), an["scenarios"][src]["amount"]))
    ok("автокран: PML ≤ EML ≤ MFL", order_ok(sc), [sc[k]["amount"] for k in ("pml", "eml", "mfl")])
    ok("автокран: PML — крупная авария с ремонтом (50 %), без слов про отсутствие защиты",
       sc["pml"]["pct"] == 50.0 and "авария" in sc["pml"]["what"] and "нет" not in sc["pml"]["what"].split("—")[0]
       and "противоугон" not in sc["pml"]["what"], sc["pml"])
    ok("автокран без противоугонной: EML 100 % — угон или гибель, подпись про отсутствие системы",
       sc["eml"]["pct"] == 100.0 and "угон" in sc["eml"]["what"] and "нет" in sc["eml"]["what"], sc["eml"])
    ok("автокран: MFL 100 % — защита не сработала", sc["mfl"]["pct"] == 100.0 and "не сработала" in sc["mfl"]["what"])
    ret = an["retention"]
    ok("лимит удержания = risk_analytics", sc["retention"]["limit"] == ret["retention_limit"]
       and sc["retention"]["known"] is (ret["retention_limit"] is not None), (sc["retention"], ret.get("retention_limit")))
    lim = sc["retention"]["limit"]
    ok("удержание сравнивается с EML: compared_with = eml, превышение = EML − лимит",
       sc["retention"]["compared_with"] == "eml"
       and (lim is None or sc["retention"]["eml_excess"] == max(sc["eml"]["amount"] - lim, 0)), sc["retention"])
    ok("MFL сверх удержания — отдельной справкой (mfl_excess)",
       lim is None or sc["retention"]["mfl_excess"] == max(sc["mfl"]["amount"] - lim, 0), sc["retention"])
    ok("что взято по умолчанию — в assumptions с пометкой (противоугонная влияет на EML)",
       any(x["code"] == "as_protection_veh_eml" and "EML" in x["text"] for x in sc["assumptions"])
       and all("по умолчанию" in x["text"] for x in sc["assumptions"]), sc["assumptions"])
    ok("how: честно о перестановке названий в risk_analytics",
       any("переставлены" in h and "PML акта = EML модуля" in h for h in sc["how"]), sc["how"])
    ok("calibrated = 0", sc["calibrated"] == 0 and sc["pml"]["calibrated"] == 0)
    rows = {r["label"]: r for r in a["sections"][3]["rows"]}
    ok("раздел 4: строки PML, EML, MFL и лимит удержания",
       {"PML — вероятный максимальный убыток", "EML — оценочный максимальный убыток",
        "MFL — максимально возможный убыток", "Лимит собственного удержания"} <= set(rows), list(rows))
    ok("раздел 4: определения заказчика (штатно / частично / отказ; удержание — с EML)",
       any("сработала штатно" in p and "сработала частично" in p and "отказе защиты" in p
           and "сравнивается лимит собственного удержания" in p for p in a["sections"][3]["paragraphs"]))
    ok("раздел 4: порядок строк PML, EML, MFL и плитки для экрана в том же порядке",
       [r["label"][:3] for r in a["sections"][3]["rows"] if r["label"][:3] in ("PML", "EML", "MFL")] == ["PML", "EML", "MFL"]
       and [x["name"] for x in sc["tiles"]] == ["PML", "EML", "MFL"] and sc["tiles"][0]["label"].startswith("PML —"))
    if lim is not None:
        note = rows["Лимит собственного удержания"]["note"]
        ok("строка удержания: сравнение с EML и справка по MFL", "EML" in note and "MFL" in note, note)
    st, a2 = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                        "optional": dict(CRANE_OPT, object_kind="truck_crane", protection="tracker")})
    sc2 = a2["scenarios"]
    ok("спутниковый поиск — EML ниже (75 %), PML 50 %, MFL 100 %",
       sc2["eml"]["pct"] == 75.0 and sc2["pml"]["pct"] == 50.0 and sc2["mfl"]["pct"] == 100.0
       and "противоугонная система есть" in sc2["eml"]["what"], sc2["eml"])
    ok("спутниковый поиск: PML ≤ EML ≤ MFL", order_ok(sc2))
    SCEN_REPORT["crane"] = {k: (sc[k]["amount"], sc[k]["pct"]) for k in ("pml", "eml", "mfl")}
    SCEN_REPORT["crane_tracker"] = {k: (sc2[k]["amount"], sc2[k]["pct"]) for k in ("pml", "eml", "mfl")}

    st, a = call("POST", "/act/make", {"lang": "ru", "must": WH_MUST, "optional": WH_OPT})
    sc = a["scenarios"]
    with db.tx() as con:
        an = ra_same(con, {"class_code": "9", "product_code": "0808", "object_type": "Склад",
                           "sum_insured": WH_MUST["sum_insured"], "object_value": WH_MUST["object_value"],
                           "region": WH_MUST["region"], "activity": "warehouse"},
                     {"protection": "alarm", "losses_3y": {"count": 0, "small_count": 0}})
    ok("склад класса 9: правило класса 9", sc["available"] and sc["rule"] == "property9", sc)
    ok("склад класса 9: PML/EML/MFL акта = EML/PML/MFL risk_analytics",
       all(sc[s.lower()]["amount"] == round(an["scenarios"][MAP_RA[s]]["amount"]) for s in ("PML", "EML", "MFL")),
       ([sc[s.lower()]["amount"] for s in ("PML", "EML", "MFL")],
        [an["scenarios"][s]["amount"] for s in ("PML", "EML", "MFL")]))
    ok("склад класса 9: PML ≤ EML ≤ MFL", order_ok(sc), [sc[k]["amount"] for k in ("pml", "eml", "mfl")])
    ok("склад класса 9: подписи — состояние защиты и «помещения не указаны»",
       sc["pml"]["what"].startswith("защита сработала штатно") and sc["eml"]["what"].startswith("защита сработала частично")
       and sc["mfl"]["what"].startswith("защита не сработала") and "не указаны" in sc["pml"]["what"],
       [sc[k]["what"] for k in ("pml", "eml", "mfl")])
    ok("склад: сумма ниже стоимости — доля в объяснении", any("0,8333" in h for h in sc["how"]), sc["how"])
    SCEN_REPORT["wh9"] = {k: (sc[k]["amount"], sc[k]["pct"]) for k in ("pml", "eml", "mfl")}
    must8 = {"product_code": "0807", "sum_insured": 5_000_000_000, "object_value": 5_000_000_000, "region": "Ташкент"}
    st, a = call("POST", "/act/make", {"lang": "ru", "must": must8,
                                       "optional": {"object_kind": "warehouse", "seismic_zone": 9,
                                                    "construction": "reinforced"}})
    with db.tx() as con:
        an = ra_same(con, {"class_code": "8", "product_code": "0807", "object_type": "Склад", "sum_insured": 5e9,
                           "object_value": 5e9, "region": "Ташкент", "construction": "reinforced",
                           "activity": "warehouse"}, {"seismic_zone": 9})
    sc = a["scenarios"]
    ok("склад класса 8 в 9-балльной зоне: сценарии = risk_analytics (со сменой названий)",
       sc["rule"] == "property8"
       and all(sc[s.lower()]["amount"] == round(an["scenarios"][MAP_RA[s]]["amount"]) for s in ("PML", "EML", "MFL")),
       sc)
    ok("склад класса 8 (9 баллов): PML ≤ EML ≤ MFL", order_ok(sc), [sc[k]["amount"] for k in ("pml", "eml", "mfl")])
    ok("склад класса 8 (9 баллов): формула названа сценарием акта",
       all((sc[k]["formula"] or "").startswith(k.upper() + " =") for k in ("pml", "eml", "mfl")),
       [sc[k]["formula"] for k in ("pml", "eml", "mfl")])
    ok("склад класса 8 (9 баллов): MFL — пожар всего объекта (отсеки не указаны)",
       "пожар" in sc["mfl"]["what"] and "отсеки не указаны" in sc["mfl"]["what"], sc["mfl"]["what"])
    SCEN_REPORT["wh8_zone9"] = {k: (sc[k]["amount"], sc[k]["pct"]) for k in ("pml", "eml", "mfl")}
    # сейсмозона не указана: MFL — полное уничтожение, подпись говорит именно это
    st, a = call("POST", "/act/make", {"lang": "ru", "must": must8,
                                       "optional": {"object_kind": "warehouse", "construction": "reinforced",
                                                    "protection": "sprinkler"}})
    sc = a["scenarios"]
    ok("склад класса 8 без сейсмозоны: PML ≤ EML ≤ MFL", order_ok(sc), [sc[k]["amount"] for k in ("pml", "eml", "mfl")])
    ok("склад класса 8 без сейсмозоны: MFL — «полное уничтожение: сейсмозона не указана», 100 %",
       "полное уничтожение" in sc["mfl"]["what"] and "сейсмозона не указана" in sc["mfl"]["what"]
       and sc["mfl"]["pct"] == 100.0 and "полное уничтожение" in sc["mfl"]["formula"], sc["mfl"])
    ok("склад класса 8 без сейсмозоны: PML и EML — пожар, отсеки не указаны (весь объект)",
       all("пожар" in sc[k]["what"] and "отсеки не указаны" in sc[k]["what"] for k in ("pml", "eml")),
       [sc[k]["what"] for k in ("pml", "eml")])
    SCEN_REPORT["wh8_sprinkler"] = {k: (sc[k]["amount"], sc[k]["pct"]) for k in ("pml", "eml", "mfl")}
    pcts = [sc[k]["pct_text"] for k in ("pml", "eml", "mfl")]
    ok("проценты сценариев — одинаковое число знаков", len({len(re.sub(r"[^\d,.]", "", p).partition(",")[2]) for p in pcts}) == 1,
       pcts)
    st, a = call("POST", "/act/make", {"lang": "ru", "must": dict(WH_MUST, product_code="0701"), "optional": {}})
    ok("груз (класс 7): сценарий честно «не считается»", st == 200 and a["scenarios"]["available"] is False
       and "не считается" in a["scenarios"]["note"] and a["scenarios"]["pml"] is None, a.get("scenarios"))
    ok("груз: в разделе 4 строка «не считается»",
       any(r["label"] == "PML / EML / MFL" and r["value"] == "не считается" for r in a["sections"][3]["rows"]))
    # собственных средств нет — удержание «не задан»
    with db.tx() as con:
        saved = db.rows(con, "SELECT * FROM company_financials")
        con.execute("DELETE FROM company_financials")
    try:
        st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT})
    finally:
        with db.tx() as con:
            for r in saved:
                cols = list(r)
                con.execute(f"INSERT INTO company_financials ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [r[c] for c in cols])
    ret = a["scenarios"]["retention"]
    ok("без собственных средств и резервов — лимит «не задан»",
       ret["known"] is False and ret["limit"] is None and "не задан" in ret["basis"], ret)


def check_documents():
    print("22. Разбор документов DOCX/XLSX/PDF с текстом — без модели")
    fresh()
    model_on(True)
    REPLY["text"] = CRANE_REPLY
    CALLS.clear()
    st, b = upload([("contract.docx", DOCX_MIME, CONTRACT.read_bytes())], {"lang": "ru", "product_code": "0808"})
    ok("учебный договор принят", st == 200 and b.get("ok"), (st, b))
    ok("модель не вызывалась", not CALLS, len(CALLS))
    rec = {(r["key"], r["value"]): r for r in b["recognized"]}
    ok("recognized: страховая сумма, стоимость, срок, регион, тип, конструкция, год",
       {("sum_insured", "4 200 000 000"), ("object_value", "5 000 000 000"), ("term_days", "365"),
        ("region", "город Ташкент"), ("object_type", "склад готовой продукции"), ("construction", "кирпич"),
        ("year", "2012")} <= set(rec), list(rec))
    ok("у каждого значения source = document и «проверьте»",
       all(r["source"] == "document" and r["check_label"] == "проверьте" and "из документа" in r["note"]
           for r in b["recognized"]))
    pf = b.get("prefill") or {}
    ok("prefill: сумма, стоимость, регион, срок — с источником",
       pf.get("sum_insured", {}).get("value") == 4_200_000_000 and pf.get("object_value", {}).get("value") == 5e9
       and pf.get("region", {}).get("code") == "tashkent_city" and pf.get("term_days", {}).get("value") == 365
       and all(v["source"] == "document" and v["check_label"] == "из документа, проверьте" for v in pf.values()), pf)
    ok("файл помечен как разобранный, в модель не ушёл",
       b["files"][0]["parsed"] and not b["files"][0]["read_by_ai"] and b["files"][0]["view"] == "document")
    ok("ИНН организации из договора не взят", "301234567" not in _json.dumps(b, ensure_ascii=False))
    st, a = call("POST", "/act/make", {"session": b["session"], "lang": "ru",
                                       "must": dict(WH_MUST, sum_insured=4_000_000_000, object_value=5_000_000_000),
                                       "optional": {}})
    dk = {d["key"]: d for d in a["discrepancies"]}
    ok("расхождение: страховая сумма в документе и во вводе", "sum_insured" in dk
       and dk["sum_insured"]["priority"] == "document" and "4 200 000 000" in dk["sum_insured"]["text"], dk)
    ok("стоимость совпала — расхождения нет", "object_value" not in dk)
    ok("документ засчитан как представленный", a["inspection"]["documents"] is True)
    s1 = {r["label"]: r for r in a["sections"][0]["rows"]}
    ok("раздел 1: конструкция из документа", s1.get("Конструкция, материал стен", {}).get("value") == "кирпич", s1)

    # ФИО, паспорт, ПИНФЛ, адрес проживания — не извлекаются и не хранятся
    lines = ["ЗАЯВЛЕНИЕ НА СТРАХОВАНИЕ ИМУЩЕСТВА", "Страхователь: Иванов Иван Иванович",
             "Паспорт: AA1234567", "ПИНФЛ: 31234567890123", "Адрес проживания: г. Ташкент, ул. Навои, 5",
             "Телефон: +998 90 123 45 67", "Страховая сумма: 1 000 000 000 сум", "Материал стен: кирпич"]
    st, b = upload([("zayavlenie.docx", DOCX_MIME, docx_bytes(lines))], {"lang": "ru", "product_code": "0808"})
    dump = _json.dumps(b, ensure_ascii=False)
    ok("заявление разобрано: сумма взята", any(r["key"] == "sum_insured" for r in b.get("recognized") or []), dump[:400])
    ok("ФИО, паспорт, ПИНФЛ, адрес проживания и телефон не извлечены",
       not any(x in dump for x in ("Иванов", "AA1234567", "31234567890123", "Навои", "123 45 67")), dump[:600])
    with db.tx() as con:
        stored = _json.dumps(db.rows(con, "SELECT result_json, files_json FROM act_uploads WHERE id=?", b["session"]),
                             ensure_ascii=False)
        journal = _json.dumps(db.rows(con, "SELECT detail FROM audit WHERE entity=?", f"act_upload:{b['session']}"),
                              ensure_ascii=False)
    ok("в базе и в журнале данных людей нет",
       not any(x in stored + journal for x in ("Иванов", "AA1234567", "31234567890123", "Навои")))
    ok("в журнале — только счётчики", "parsed_docs" in journal and "1 000 000 000" not in journal, journal[:300])

    # XLSX-выгрузка по технике: марка, модель, год, VIN, госномер
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for row in (["Заявление на страхование спецтехники"], ["Страховая сумма", "2 945 000 000 сум"],
                ["Стоимость имущества", "3 100 000 000 сум"], ["Марка", "XCMG"], ["Модель", "QY50K5D"],
                ["Год выпуска", "2026"], ["Заводской номер (VIN)", "LXGCPA393TA006921"],
                ["Государственный номер", "01 A 123 BC"]):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    CALLS.clear()
    st, b = upload([("vygruzka.xlsx", XLSX_MIME, buf.getvalue())], {"lang": "ru", "product_code": "0318"})
    got = {(r["key"], r["value"]) for r in b.get("recognized") or []}
    ok("XLSX: марка, модель, год, VIN, госномер, сумма — из документа",
       {("brand", "XCMG"), ("model", "QY50K5D"), ("year", "2026"), ("serial_no", "LXGCPA393TA006921"),
        ("reg_no", "01 A 123 BC"), ("sum_insured", "2 945 000 000")} <= got and not CALLS, got)

    # PDF с текстовым слоем — парсером; скан — модели
    doc = pymupdf.open()
    page = doc.new_page()
    font = act._fonts()[0]
    tw = pymupdf.TextWriter(page.rect)
    for k, ln in enumerate(["ДОГОВОР СТРАХОВАНИЯ ИМУЩЕСТВА № 7/2026", "Страховая сумма: 1 000 000 000 сум",
                            "Стоимость имущества: 1 100 000 000 сум",
                            "Адрес объекта: Самаркандская область, г. Самарканд", "Срок страхования: 12 месяцев"]):
        tw.append((72, 72 + 18 * k), ln, font=font, fontsize=11)
    tw.write_text(page)
    CALLS.clear()
    st, b = upload([("dogovor.pdf", "application/pdf", doc.tobytes())], {"lang": "ru", "product_code": "0808"})
    ok("PDF с текстом разобран без модели", st == 200 and not CALLS and b["files"][0]["parsed"]
       and (b.get("prefill") or {}).get("region", {}).get("code") == "samarkand", b.get("prefill"))
    CALLS.clear()
    st, b = upload([("scan.pdf", "application/pdf", pdf_pages(1))], {"lang": "ru"})
    ok("скан PDF без текста — читает модель", st == 200 and len(CALLS) == 1 and not b["files"][0]["parsed"])

    # ограничения: zip-бомба, DTD, макросы
    bomb = docx_bomb(60)
    st, b = upload([("bomb.docx", DOCX_MIME, bomb)], {"lang": "ru"})
    ok("zip-бомба в DOCX отклонена до распаковки", st == 422 and b["rejected"]
       and "слишком большой" in b["rejected"][0]["error"] and len(bomb) < 1024 * 1024, (st, b, len(bomb)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><w:document/>')
    st, b = upload([("dtd.docx", DOCX_MIME, buf.getvalue())], {"lang": "ru"})
    ok("DOCX с DTD/ENTITY отклонён", st == 422 and "DTD" in b["rejected"][0]["error"], b)
    st, b = upload([("macro.docx", DOCX_MIME, docx_bytes(["Страховая сумма: 1 000 000 000 сум"],
                                                          {"word/vbaProject.bin": b"\x00" * 100}))], {"lang": "ru"})
    ok("макросы не исполняются — только пометка", st == 200 and any("макрос" in n for n in b["notes"]), b.get("notes"))
    set_limits(doc_max_unzip_mb=1)
    try:
        st, b = upload([("contract.docx", DOCX_MIME, docx_bomb(2))], {"lang": "ru"})
        ok("предел распаковки — из настроек (1 МБ)", st == 422, st)
    finally:
        clear_settings()
    st, b = upload([("x.zip", "application/zip", docx_bomb(1).replace(b"word/document.xml", b"other/documen.xml"))],
                   {"lang": "ru"})
    ok("прочий zip — формат не принимается", st == 422 and "DOCX или XLSX" in b["rejected"][0]["error"], b)


def check_franchise_apply():
    print("23. Франшиза: предложение по основанию, франшиза сотрудника, обязательный вид")
    from app import franchise as frm
    from app.risk_analytics import load_thresholds
    fresh()
    with db.tx() as con:
        th = load_thresholds(con)
        base, floor, applied = expected_rate(con, 20)            # умеренный уровень: без фото, открытая площадка
    S = CRANE_MUST["sum_insured"]
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": CRANE_OPT})
    fr = a["franchise"]
    ok("без оснований: «Франшиза не требуется», статус none, премия не меняется",
       fr["text"] == "Франшиза не требуется" and fr["status"] == "none" and not fr["applied"]
       and fr["premium_after"] == fr["premium_before"] == a["premium"]["amount"] and fr["delta"] == 0, fr)
    ok("без оснований: альтернативы есть (мероприятия, сумма к стоимости)",
       {x["code"] for x in fr["alternatives"]} >= {"alt_sum_up"} and fr["how"], fr["alternatives"])

    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, losses_3y={"count": 2, "small_count": 2})})
    fr = a["franchise"]
    ok("основание есть: статус proposed, тип безусловная", fr["status"] == "proposed"
       and fr["type"] == "unconditional" and fr["needed"], fr)
    ok("размер — внутри вилки порогов", fr["size"]["from_pct"] <= fr["size_pct"] <= fr["size"]["to_pct"]
       and fr["size_amount"] == round(S * fr["size_pct"] / 100), fr)
    ok("множитель — из franchise.what_if (экспертная кривая)", abs(fr["multiplier"] - frm._mult(th, fr["size_pct"])) < 1e-4,
       (fr["multiplier"], frm._mult(th, fr["size_pct"])))
    rate = a["rate"]["applied_pct"]
    want_rate = max(round(rate * fr["multiplier"], 4), a["rate"]["min_pct"] or 0)
    ok("премия с франшизой = ставка акта × множитель, не ниже минимума",
       fr["premium_after"] == round(want_rate / 100 * S) and fr["rate_after"] == want_rate, (fr, want_rate))
    ok("предложенная франшиза в премию акта не включена", a["premium"]["amount"] == fr["premium_before"]
       and a["premium"]["franchise_applied"] is False and "андеррайтер" in fr["text"])

    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, deductible={"pct": 1, "type": "unconditional"})})
    fr = a["franchise"]
    hand_rate = max(round(applied * 0.85, 4), floor)          # 1 % по справочнику коэффициентов: ×0,85
    hand = round(hand_rate / 100 * S)
    ok("своя франшиза сотрудника 1 %: премия пересчитана руками", a["premium"]["amount"] == hand
       and fr["premium_after"] == hand and fr["premium_before"] == round(applied / 100 * S), (a["premium"], hand))
    ok("текст «Франшиза применена по решению сотрудника»",
       fr["text"].startswith("Франшиза применена по решению сотрудника") and fr["applied"]
       and fr["applied_by"] == "employee", fr["text"])
    rows = {r["label"]: r for r in a["sections"][3]["rows"]}
    ok("раздел 4: та же премия и ставка с франшизой",
       rows["Страховая премия"]["value"] == act.money(hand, "ru")
       and rows["Ставка с учётом франшизы"]["value"] == act.pct(hand_rate, "ru")
       and act.money(round(applied / 100 * S), "ru") in rows["Страховая премия"]["note"], rows.get("Страховая премия"))
    ok("андеррайтеру — подтвердить франшизу", any("франшизу, применённую" in c for c in a["decision"]["checks"]))
    ok("how: множитель и ставка акта, премия старого движка не переносится",
       any("what_if" in h for h in fr["how"]) and any("не переносится" in h for h in fr["how"]), fr["how"])
    applied_id = a["id"]
    st, a2 = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                        "optional": dict(CRANE_OPT, deductible={"amount": 29_450_000})})
    ok("франшиза суммой = 1 %", a2["franchise"]["size_pct"] == 1.0 and a2["premium"]["amount"] == hand, a2["franchise"])
    st, a2 = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                        "optional": dict(CRANE_OPT, deductible={"pct": 7})})
    ok("выше потолка класса 3 (5 %) — предупреждение, решение андеррайтера",
       a2["franchise"]["warning"] and "потолка" in a2["franchise"]["warning"] and a2["franchise"]["applied"], a2["franchise"])
    st, a2 = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                        "optional": dict(CRANE_OPT, deductible={"pct": 80})})
    ok("франшиза 80 % — ошибка ввода 422", st == 422 and "deductible" in a2.get("errors", {}), a2)
    st, a2 = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                        "optional": dict(CRANE_OPT, deductible={"pct": 1, "amount": 5})})
    ok("pct и amount вместе — 422", st == 422, a2)
    must = {"product_code": "0820", "sum_insured": 1_000_000_000, "object_value": 1_000_000_000, "region": "Ташкент"}
    st, a2 = call("POST", "/act/make", {"lang": "ru", "must": must,
                                        "optional": {"deductible": {"pct": 1}, "location": "construction"}})
    fr = a2["franchise"]
    ok("обязательный вид: франшиза сотрудника не применена, премия по акту",
       st == 200 and fr["status"] == "statutory" and not fr["applied"]
       and a2["premium"]["amount"] == a2["premium"]["before_franchise"] and "обязательным видам" in fr["warning"], fr)
    # низкий уровень: ставка уже на минимуме продукта — франшиза премию не снижает, и акт говорит об этом
    low = dict(CRANE_OPT, object_kind="truck_crane", year=date.today().year, condition="new",
               documents_provided=True, deductible={"pct": 1})
    st, a2 = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": low})
    fr = a2["franchise"]
    ok("ставка на минимуме: премия с франшизой не ниже минимальной ставки",
       a2["risk"]["level"] == "low" and fr["floor_applied"] and a2["premium"]["amount"] == round(floor / 100 * S)
       and "минимальную ставку" in fr["text"], (a2["risk"]["level"], fr))
    return applied_id, hand


def check_measures():
    print("24. Рекомендации страхователю")
    fresh()
    with db.tx() as con:
        _b, floor, applied = expected_rate(con, 20)
    S = CRANE_MUST["sum_insured"]
    P = round(applied / 100 * S)
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, object_kind="truck_crane")})
    ms = {m["code"]: m for m in a["measures"]}
    ok("спецтехника на открытой площадке: охраняемая стоянка и спутниковый мониторинг",
       {"sp_parking_guarded", "sp_gps", "sp_crane_setup", "sp_operator"} <= set(ms), list(ms))
    ok("эффект мероприятия — из справочника коэффициентов (−10 %)",
       ms["sp_parking_guarded"]["effect_pct"] == -10.0 and ms["sp_gps"]["effect_pct"] == -10.0, ms["sp_gps"])
    ok("без эффекта — «на ставку не влияет, снижает вероятность убытка»",
       ms["sp_operator"]["premium_delta"] is None and "снижает вероятность" in ms["sp_operator"]["effect_text"])
    want = max(round(P * 0.9 * 0.9), round(floor / 100 * S))
    summ = a["measures_summary"]
    ok("скидки перемножаются и не ниже минимальной ставки продукта",
       summ["premium_after"] == want and summ["premium_before"] == P, (summ, want))
    ok("каждое мероприятие: что, зачем, срок, calibrated=0",
       all(m["text"] and m["why"] and m["deadline_days"] and m["calibrated"] == 0 for m in a["measures"]))
    s5 = a["sections"][4]
    li = next((x for x in s5["lists"] if x["title"] == "Рекомендации страхователю"), None)
    ok("раздел 5: подраздел «Рекомендации страхователю»", li and len(li["items"]) == len(a["measures"])
       and "Зачем:" in li["items"][0] and "Срок:" in li["items"][0], li)
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, object_kind="truck_crane", guard=True)})
    ok("под охраной — охраняемая стоянка не нужна", "sp_parking_guarded" not in {m["code"] for m in a["measures"]})

    st, a = call("POST", "/act/make", {"lang": "ru", "must": WH_MUST, "optional": WH_OPT})
    ms = {m["code"]: m for m in a["measures"]}
    ok("склад класса 9: мероприятия таблицы preventive_measures и хранение на стеллажах",
       {"burglary_protection", "inventory_control", "wh_storage"} <= set(ms)
       and ms["burglary_protection"]["source"] == "preventive_measures", list(ms))
    ok("склад: сумма 1 млрд — охранная сигнализация обязательна, срок 60 дней",
       ms["burglary_protection"]["mandatory"] and ms["burglary_protection"]["deadline_days"] == 60,
       ms["burglary_protection"])
    st, u = call("GET", f"/act/{a['id']}", params={"lang": "uz"})
    ok("uz: мероприятия таблицы переведены", not re.search(r"[А-Яа-яЁё]", " ".join(
        m["text"] + m["why"] for m in u["measures"])), [m["text"] for m in u["measures"]])
    must8 = {"product_code": "0807", "sum_insured": 5_000_000_000, "object_value": 5_000_000_000, "region": "Ташкент"}
    st, a = call("POST", "/act/make", {"lang": "ru", "must": must8,
                                       "optional": {"object_kind": "warehouse", "construction": "wood"}})
    ms = {m["code"]: m for m in a["measures"]}
    ok("склад класса 8 из дерева: огнезащитная обработка со скидкой (из движка)",
       "fire_treatment" in ms and ms["fire_treatment"]["effect_pct"] and ms["fire_treatment"]["effect_pct"] < 0
       and ms["fire_treatment"]["mandatory"], ms.get("fire_treatment"))
    must = {"product_code": "0820", "sum_insured": 1_000_000_000, "object_value": 1_000_000_000, "region": "Ташкент"}
    st, a = call("POST", "/act/make", {"lang": "ru", "must": must, "optional": {}})
    ok("обязательный вид: скидок на премию нет", all(m["premium_delta"] is None for m in a["measures"]))


def check_new_langs(aid):
    print("25. Новые блоки на трёх языках")
    for lang in ("uz", "en"):
        st, a = call("GET", f"/act/{aid}", params={"lang": lang})
        fr = a["franchise"]
        texts = ([r["label"] for s in a["sections"] for r in s["rows"]]
                 + [r.get("note") or "" for r in a["sections"][3]["rows"]]
                 + [li["title"] for s in a["sections"] for li in s["lists"]]
                 + [p for s in a["sections"] for p in s["paragraphs"]]
                 + [fr["text"], fr["type_label"] or ""] + fr["how"] + [x["text"] for x in fr["alternatives"]]
                 + [a["scenarios"][k]["what"] for k in ("pml", "eml", "mfl")] + a["scenarios"]["how"]
                 + [x["text"] for x in a["scenarios"]["assumptions"]] + [a["scenarios"]["retention"]["basis"]]
                 + [m["text"] + " " + m["why"] + " " + m["effect_text"] for m in a["measures"]]
                 + [a["measures_summary"]["text"] or ""])
        cyr = [x for x in texts if re.search(r"[А-Яа-яЁё]", x or "")]
        ok(f"{lang}: сценарии, франшиза, мероприятия без кириллицы", not cyr, cyr[:4])
    st, a = call("GET", f"/act/{aid}", params={"lang": "en"})
    ok("en: франшиза применена сотрудником", a["franchise"]["text"].startswith("Deductible applied by staff"))


def check_new_files(aid, hand):
    print("26. Word и PDF содержат новые блоки и ту же премию")
    st, blob, h = call("GET", f"/act/{aid}.docx", raw=True)
    xml = zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml").decode("utf-8")
    plain = re.sub(r"<[^>]+>", "", xml)
    need = ["PML — вероятный максимальный убыток", "EML — оценочный максимальный убыток",
            "MFL — максимально возможный убыток", "Лимит собственного удержания", "Рекомендации страхователю",
            "Франшиза применена по решению сотрудника", "Ставка с учётом франшизы", act.money(hand, "ru")]
    ok("DOCX: сценарии, франшиза, рекомендации, премия с франшизой", all(x in plain for x in need),
       [x for x in need if x not in plain])
    st, blob, h = call("GET", f"/act/{aid}.pdf", raw=True)
    text = pdf_text(pymupdf.open(stream=blob, filetype="pdf"))
    flat = [x.replace(" ", " ") for x in need]           # pdf_text приводит неразрывный пробел к обычному
    ok("PDF: те же блоки и премия", all(x in text for x in flat), [x for x in flat if x not in text])
    st, a = call("GET", f"/act/{aid}")
    ok("премия в JSON, в разделе 4 и в файлах одна", a["premium"]["amount"] == hand
       and a["premium"]["text"] == act.money(hand, "ru"))


def check_speed():
    print("27. Акт формируется быстро и без сети")
    import time as _t
    fresh()
    CALLS.clear()
    t0 = _t.monotonic()
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, deductible={"pct": 1})})
    sec = _t.monotonic() - t0
    ok("акт со всеми блоками — меньше 2 секунд, без модели", st == 200 and sec < 2 and not CALLS, sec)
    with db.tx() as con:
        rows = db.rows(con, "SELECT detail FROM audit WHERE entity=?", f"act:{a['id']}")
    ok("в журнале — статус франшизы и счётчики, без значений",
       rows and '"franchise": "applied"' in rows[0]["detail"] and "29 450 000" not in rows[0]["detail"], rows)


# ------------------------------------------------------------------ 28–35. замечания контролёра (30.09.2026)

def xlsx_big(rows, cols, sheets=1) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    words = ["склад", "сумма", "Ташкент", "объект", "qiymat", "ombor", "value", "итого", "страхование"]
    for s in range(sheets):
        if s:
            ws = wb.create_sheet(f"Лист {s + 1}")
        for r in range(rows):
            ws.append([f"{words[(r + c) % len(words)]} {r}-{c} {words[(r * c) % len(words)]}" for c in range(cols)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def check_doc_limits():
    print("28. Разбор документов: пределы до разбора, срок внутри циклов, не больше двух одновременно")
    import threading
    import time as _t
    from app import act_extras as ax, docparse as D, ingest
    fresh()
    CALLS.clear()
    for rows, cols, sheets, name in ((500, 60, 1, "500 × 60"), (2000, 3, 3, "2000 строк × 3 листа")):
        blob = xlsx_big(rows, cols, sheets)
        t0 = _t.monotonic()
        st, b = upload([("big.xlsx", XLSX_MIME, blob)], {"lang": "ru", "product_code": "0808"})
        sec = _t.monotonic() - t0
        ok(f"XLSX {name} ({len(blob) // 1024} КБ) — быстрее 3 с", st == 200 and sec < 3, (st, round(sec, 2)))
        ok(f"XLSX {name}: честная пометка «прочитана только часть»",
           any("только часть документа" in n for n in b.get("notes") or []) and b["files"][0]["parsed"], b.get("notes"))
    ok("большие XLSX в модель не уходили", not CALLS, len(CALLS))
    # пределы работают до разбора: листов, строк, колонок, длина ячейки
    folder = Path(tempfile.mkdtemp(prefix="act-lim-"))
    try:
        p = folder / "w.xlsx"
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["x" * 900, "короткая"])
        ws.append([f"c{c}" for c in range(40)])
        for r in range(300):
            ws.append([f"r{r}", "a", "b"])
        for s in range(4):
            wb.create_sheet(f"S{s}").append(["лист", s])
        wb.save(p)
        got = ax.read_limited(p, {})
        t1 = got["tables"][0]["rows"]
        ok("предел: не больше 3 листов", len(got["tables"]) == 3, [t["name"] for t in got["tables"]])
        ok("предел: не больше 200 строк и 30 колонок с листа",
           len(t1) == 200 and max(len(r) for r in t1) == 30 and got["tables"][0]["обрезан"],
           (len(t1), max(len(r) for r in t1)))
        ok("предел: ячейка и строка не длиннее 500 знаков",
           all(len(" | ".join(r)) <= 500 for r in t1) and all(len(ln) <= 500 for ln in got["text"].splitlines()))
        ok("предел: отметка truncated", got["truncated"] is True)
        small = ax.read_limited(p, {"doc_max_cells": 100})
        ok("предел ячеек на файл — из настроек (100)", sum(len([c for c in r if c]) for t in small["tables"]
                                                             for r in t["rows"]) <= 100, small["tables"][0]["rows"][:2])
        # учебный договор: тот же prefill и те же значения, что у прежнего разбора без пределов
        with db.tx() as con:
            old = ax.parse_document(con, CONTRACT, "8")
            new = ax.parse_document_limited(con, CONTRACT, "8", {})
        ok("flow150_contract.docx: prefill и значения те же, что без пределов",
           old["prefill"] == new["prefill"] and old["items"] == new["items"] and not new["notes"], (old, new))
        r_old, r_new = ingest.read_file(CONTRACT), ax.read_limited(CONTRACT, {})
        ok("потоковое чтение DOCX = ingest.read_docx (текст и таблицы)",
           r_old["text"] == r_new["text"] and r_old["tables"] == r_new["tables"])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    # кэш свёртки: поведение прежнее, список карты — свой у каждого вызова
    s = "Sugʻurta summasi: 1 000 000 сум"
    f1, i1 = D.fold_map(s)
    f2, i2 = D._fold_map_raw(s)
    i1.append(-1)
    ok("fold_map из кэша = прежний расчёт, карта не делится между вызовами",
       (f1, i1[:-1]) == (f2, i2) and D.fold_map(s)[1] == i2 and D.fold(s) == f2)
    # срок: проверка внутри цикла освобождает поток — разбор возвращается, поток не остаётся висеть
    orig = ingest.detect_language
    ingest.detect_language = lambda text: (_t.sleep(0.4), orig(text))[1]
    set_limits(doc_parse_sec=0.2)
    try:
        before = threading.active_count()
        t0 = _t.monotonic()
        st, b = upload([("contract.docx", DOCX_MIME, CONTRACT.read_bytes())], {"lang": "ru", "product_code": "0808"})
        sec = _t.monotonic() - t0
        ok("срок одного файла: «не разобран: слишком большой», запрос продолжается",
           st == 200 and not b["files"][0]["parsed"] and any("слишком большой" in n for n in b["notes"])
           and sec < 2, (st, b.get("notes"), sec))
        ok("поток разбора освобождён (лишних потоков нет)", threading.active_count() <= before,
           (before, threading.active_count()))
        with db.tx() as con:
            j = db.rows(con, "SELECT detail FROM audit WHERE entity=? AND action='акт: документ не разобран'",
                        f"act_upload:{b['session']}")
        ok("превышение срока — в журнале", j and "timeout" in j[0]["detail"], j)
    finally:
        ingest.detect_language = orig
        clear_settings()
    # срок на все документы запроса: второй файл уже не разбирается
    ingest.detect_language = lambda text: (_t.sleep(0.35), orig(text))[1]
    set_limits(doc_parse_sec=5, doc_parse_total_sec=0.3)
    try:
        st, b = upload([("a.docx", DOCX_MIME, CONTRACT.read_bytes()), ("b.docx", DOCX_MIME, CONTRACT.read_bytes())],
                       {"lang": "ru", "product_code": "0808"})
        ok("общий срок запроса: оба файла помечены, ответ есть",
           st == 200 and not any(f["parsed"] for f in b["files"])
           and len(b["documents"]) == 2 and all(d["notes"] for d in b["documents"]), (st, b.get("documents")))
    finally:
        ingest.detect_language = orig
        clear_settings()
    # не больше двух документов одновременно: оба места заняты — файл честно «сервер занят»
    ok("мест для разбора — два", ax.PARSE_SLOTS == 2)
    took = [ax.parse_slot(0), ax.parse_slot(0)]
    ok("третий документ места не получает", not ax.parse_slot(0))
    set_limits(doc_parse_total_sec=0.3)
    try:
        st, b = upload([("contract.docx", DOCX_MIME, CONTRACT.read_bytes())], {"lang": "ru", "product_code": "0808"})
        ok("семафор занят: «сервер занят разбором», запрос не падает",
           st == 200 and any("сервер занят" in n for n in b["notes"]) and not b["files"][0]["parsed"], b.get("notes"))
    finally:
        for x in took:
            if x:
                ax.parse_slot_release()
        clear_settings()
    st, b = upload([("contract.docx", DOCX_MIME, CONTRACT.read_bytes())], {"lang": "ru", "product_code": "0808"})
    ok("места освобождены — договор снова разбирается", st == 200 and b["files"][0]["parsed"])
    errs = ae.check_settings({"limits": dict(ae.DEFAULT_SETTINGS["limits"], doc_max_rows=5)})
    ok("настройки: предел строк проверяется", any("doc_max_rows" in e for e in errs), errs)
    ok("настройки по умолчанию: 5000 ячеек, 200 строк, 30 колонок, 3 листа, 500 знаков, 200 000 знаков, 5 с и 10 с",
       {k: ae.DEFAULT_SETTINGS["limits"][k] for k in ax.DOC_LIMITS} == ax.DOC_LIMITS
       and not ae.check_settings({"limits": dict(ae.DEFAULT_SETTINGS["limits"])}))


def check_dtd_prolog():
    print("29. DTD/ENTITY ищется во всём прологе XML, а не в первых 4096 байтах")
    from app import act_extras as ax
    fresh()
    xml = ('<?xml version="1.0"?><!--' + "x" * 5000 + '--><!DOCTYPE x [<!ENTITY a "aaaa">]>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    st, b = upload([("dtd5000.docx", DOCX_MIME, buf.getvalue())], {"lang": "ru"})
    ok("комментарий 5000 байт перед DOCTYPE — файл отклонён", st == 422 and "DTD" in b["rejected"][0]["error"], (st, b))
    long_c = b'<?xml version="1.0"?><!--' + b"-" * 3 + b"y" * 200_000 + b'-->\n<!ENTITY e "x"><r/>'
    ok("комментарий 200 КБ (через границы кусков) — ENTITY найдена", ax.xml_prolog_has_dtd(io.BytesIO(long_c), 10 ** 7))
    ok("обычный XML с комментарием и инструкцией — годится",
       not ax.xml_prolog_has_dtd(io.BytesIO(b'\xef\xbb\xbf<?xml version="1.0"?>\n<!-- c --><?pi x?><r><!-- <!DOCTYPE --></r>'),
                                 10 ** 6))
    ok("UTF-16 с DOCTYPE — отклоняется", ax.xml_prolog_has_dtd(
        io.BytesIO('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r><r/>'.encode("utf-16")), 10 ** 6))
    ok("DOCTYPE после корня (в тексте) не ищется — пролог закончился", not ax.xml_prolog_has_dtd(
        io.BytesIO(b"<r>" + b"z" * 10 + b"&lt;!DOCTYPE</r>"), 10 ** 6))
    st, b = upload([("contract.docx", DOCX_MIME, CONTRACT.read_bytes())], {"lang": "ru", "product_code": "0808"})
    ok("настоящий договор проверку проходит", st == 200 and b["files"][0]["parsed"])


def check_fr_proposed():
    print("30. Предложенная франшиза: минимум ставки и множитель выше 2 %")
    fresh()
    with db.tx() as con:
        _b, floor, _a = expected_rate(con, 0)
    S = CRANE_MUST["sum_insured"]
    low = dict(CRANE_OPT, object_kind="truck_crane", year=date.today().year, condition="new",
               documents_provided=True, want_lower_premium=True)
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": low})
    fr = a["franchise"]
    ok("низкий уровень + просьба клиента: франшиза предложена", fr["status"] == "proposed" and fr["size_pct"] > 0, fr)
    ok("предложенная упирается в минимум: floor_applied и то же пояснение, что у применённой",
       fr["floor_applied"] is True and fr["premium_after"] == round(floor / 100 * S)
       and "минимальную ставку продукта" in fr["text"], (fr["floor_applied"], fr["text"]))
    # высокий уровень (вилка 5–10 %, потолок класса 3 — 5 %): вилка 5 % — одно число, множитель — продолжение кривой
    fr_hi = {"code": "fr_advise_range", "needed": True, "status": "proposed", "type": "unconditional",
             "size": {"from_pct": 5.0, "to_pct": 5.0, "from_amount": S * 0.05, "to_amount": S * 0.05},
             "size_pct": 5.0, "size_amount": round(S * 0.05), "premium_before": 100, "premium_after": 90,
             "floor_applied": True, "engine": {"extrapolated": True},
             "how": [{"code": "frh_size", "params": {"from": 5.0, "to": 5.0, "pct": 5.0, "losses": 0}},
                     {"code": "frh_apply", "params": {}}]}
    text = act._fr_text(fr_hi, "ru")
    ok("вилка с равными границами — «5 %», а не «от 5 % до 5 %»", "франшизу 5 %" in text and "от 5" not in text, text)
    ok("предложенная выше 2 %: «экспертное продолжение», и пояснение о минимуме", "экспертное продолжение" in text
       and "минимальную ставку продукта" in text, text)
    how = act._fr_how_text(fr_hi["how"][0], "ru")
    ok("как посчитано: вилка «ровно 5 %»", "ровно 5 %" in how and "–" not in how, how)
    for lang in ("uz", "en"):
        tx_ = act._fr_text(fr_hi, lang)
        ok(f"{lang}: пояснения о минимуме и о 2 % переведены", not re.search(r"[А-Яа-яЁё]", tx_), tx_)


def check_alt_base():
    print("31. «Вместо франшизы можно»: все варианты — от премии без франшизы")
    fresh()
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST,
                                       "optional": dict(CRANE_OPT, object_kind="truck_crane",
                                                        deductible={"pct": 1, "type": "unconditional"})})
    fr = a["franchise"]
    P = a["premium"]["before_franchise"]
    alts = fr["alternatives"]
    ok("франшиза применена, премия акта с ней ниже базы", fr["applied"] and a["premium"]["amount"] < P, a["premium"])
    ok("у каждого варианта база — премия без франшизы",
       alts and all(x["base_premium"] == P for x in alts), [(x["code"], x.get("base_premium")) for x in alts])
    ok("premium_delta = premium − премия без франшизы",
       all(x["premium"] is None or x["premium_delta"] == x["premium"] - P for x in alts),
       [(x["code"], x["premium"], x["premium_delta"]) for x in alts])
    m = next((x for x in alts if x["code"] == "alt_measures"), None)
    want = a["measures_summary"]
    ok("мероприятия вместо франшизы: скидки от базы без франшизы",
       m is not None and m["premium"] == P + m["premium_delta"] and m["premium_delta"] < 0, (m, want))
    ok("в тексте каждого варианта — «Вместо франшизы»", all(x["text"].startswith("Вместо франшизы") for x in alts),
       [x["text"] for x in alts])
    st, u = call("GET", f"/act/{a['id']}", params={"lang": "en"})
    ok("en: «Instead of a deductible», ссылка на норму — по-английски",
       all(x["text"].startswith("Instead of a deductible") for x in u["franchise"]["alternatives"])
       and all(not re.search(r"[А-Яа-яЁё]", x["legal_ref"] or "") for x in u["franchise"]["alternatives"]),
       u["franchise"]["alternatives"])


def check_protection_other():
    print("32. Защита для класса без списка — не ошибка, а пометка")
    fresh()
    st, a = call("POST", "/act/make", {"lang": "ru", "must": dict(WH_MUST, product_code="0701"),
                                       "optional": {"protection": "alarm"}})
    ok("груз (класс 7) с protection — 200, не ошибка ввода", st == 200 and a.get("ok"), (st, a.get("errors")))
    asm = [x["text"] for x in a["scenarios"]["assumptions"]]
    ok("пометка в «принято по умолчанию»", any("для этого класса" in x and "не учтено" in x for x in asm), asm)
    ok("в разделе 4 есть список «Принято по умолчанию»",
       any(li["title"] == "Принято по умолчанию (уточните)" for li in a["sections"][3]["lists"]))
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": dict(CRANE_OPT, protection="sprinkler")})
    ok("класс 3 с кодом защиты имущества (sprinkler) — по-прежнему 422", st == 422 and "protection" in a.get("errors", {}), a)


def check_lang_fields():
    print("33. uz/en: формула и ссылка на норму — не на чужом языке")
    fresh()
    st, a = call("POST", "/act/make", {"lang": "ru", "must": CRANE_MUST, "optional": dict(CRANE_OPT, object_kind="truck_crane")})
    ok("ru: формула есть, ссылка — «Положение № 1806, п. 15»", a["scenarios"]["pml"]["formula"]
       and a["scenarios"]["retention"]["legal_ref"] == "Положение № 1806, п. 15", a["scenarios"]["retention"])
    for lang, ref in (("uz", "1806-son Nizom, 15-band"), ("en", "Regulation No. 1806, para. 15")):
        st, u = call("GET", f"/act/{a['id']}", params={"lang": lang})
        sc = u["scenarios"]
        ok(f"{lang}: formula = null у всех трёх сценариев", all(sc[k]["formula"] is None for k in ("pml", "eml", "mfl")))
        ok(f"{lang}: legal_ref удержания — на языке акта", sc["retention"]["legal_ref"] == ref, sc["retention"]["legal_ref"])
        texts = [sc["definitions"], sc["retention"]["text"] or ""] + [x["label"] + " " + x["what"] + " " + x["pct_text"]
                                                                        for x in sc["tiles"]]
        ok(f"{lang}: определения, плитки и удержание без кириллицы", not any(re.search(r"[А-Яа-яЁё]", x) for x in texts),
           texts)


def check_minor():
    print("34. Мелочи: управляющие байты, проценты, подпись года")
    src = (Path(act.__file__)).read_bytes()
    bad = [b for b in src if (b < 32 and b not in (9, 10, 13)) or b == 127]
    ok("в app/act.py нет сырых управляющих символов", not bad, bad[:5])
    ok("сигнатура zip записана как b\"PK\\x03\\x04\"", b'b"PK\\x03\\x04"' in src)
    ok("_format_of по-прежнему узнаёт DOCX", act._format_of(docx_bytes(["Страховая сумма: 1 сум"])) == "docx")
    sc = {"available": True, "order": "classic", "class_code": "9", "rule": "property9", "k": 1,
          "items": {s: {"amount": p * 10, "pct": p, "what": "sc_w_c9", "state": "sc_state_" + s.lower()}
                    for s, p in (("PML", 15.0), ("EML", 37.5), ("MFL", 100.0))},
          "retention": {"known": False}, "assumptions": []}
    v = act._scenarios_view(sc, {"class_code": "9"}, "ru")["json"]
    ok("проценты рядом — одинаково: 15,0 % · 37,5 % · 100,0 %",
       [v[k]["pct_text"] for k in ("pml", "eml", "mfl")] == ["15,0 %", "37,5 %", "100,0 %"],
       [v[k]["pct_text"] for k in ("pml", "eml", "mfl")])
    sc["items"]["EML"]["pct"] = 40.0
    v = act._scenarios_view(sc, {"class_code": "9"}, "en")["json"]
    ok("все целые — без дроби: 15% · 40% · 100%", [v[k]["pct_text"] for k in ("pml", "eml", "mfl")] == ["15%", "40%", "100%"])
    fresh()
    st, a = call("POST", "/act/make", {"lang": "ru", "must": WH_MUST, "optional": WH_OPT})
    labels = [r["label"] for r in a["sections"][0]["rows"]]
    ok("здание: «Год постройки»", "Год постройки" in labels and "Год выпуска" not in labels, labels)
    for lang, want in (("uz", "Qurilgan yili"), ("en", "Year built")):
        st, u = call("GET", f"/act/{a['id']}", params={"lang": lang})
        ok(f"{lang}: здание — «{want}»", want in [r["label"] for r in u["sections"][0]["rows"]])
    st, a = call("POST", "/act/make", {"lang": "en", "must": CRANE_MUST, "optional": CRANE_OPT})
    labels = [r["label"] for r in a["sections"][0]["rows"]]
    ok("техника: «Year of manufacture»", "Year of manufacture" in labels and "Year built" not in labels, labels)


def check_screen():
    print("35. Экран: плитки сценариев из ответа сервера, сброс пометок «из документа»")
    html = (Path(act.__file__).parent / "tg.html").read_text(encoding="utf-8")
    body = html[html.index("function actScenHtml"):html.index("function actMeasuresHtml")]
    ok("плитки берутся из scenarios.tiles (порядок и подписи сервера)", "s.tiles" in body and "x.label" in body
       and "x.pct_text" in body, body[:300])
    ok("удержание: текст сервера (сравнение с EML)", "r.text" in body)
    pre = html[html.index("function wzApplyPrefill"):html.index("function preTag")]
    ok("wzApplyPrefill: пометка остаётся только у значений из нового ответа",
       "delete CH.pre[k]" in pre and "CH.preDoc" in pre, pre[-300:])
    call_site = html[html.index("CH.preDoc = {};"):html.index("function wzApplyPrefill")]
    ok("новая загрузка без prefill тоже сбрасывает пометки", "wzApplyPrefill(d.prefill && typeof d.prefill" in call_site
       and ": {})" in call_site, call_site)


def main():
    ORIG.update(chat_raw=llm.chat_raw, enabled=llm.enabled, supports_files=llm.supports_files, post=llm._post)
    llm.chat_raw = fake_chat_raw
    llm._post = no_network
    folder = Path(tempfile.mkdtemp(prefix="surveyor-act-"))
    try:
        with temp_db("surveyor-act-test.db"):
            db.ensure_schema()
            act.DIR = folder
            guest.reset()
            check_engine()
            a, sid = check_crane()
            aid = a["id"]
            check_value()
            check_franchise()
            check_no_rate()
            check_no_model()
            check_pd()
            check_files(aid)
            check_langs(aid)
            check_polish()
            check_access(aid)
            check_bomb()
            check_threadpool()
            check_body_limit()
            check_limits()
            check_budget()
            check_sources()
            check_misc()
            check_scenarios()
            check_documents()
            fr_aid, fr_hand = check_franchise_apply()
            check_measures()
            check_new_langs(fr_aid)
            check_new_files(fr_aid, fr_hand)
            check_speed()
            check_doc_limits()
            check_dtd_prolog()
            check_fr_proposed()
            check_alt_base()
            check_protection_other()
            check_lang_fields()
            check_minor()
            check_screen()
            check_send(aid)
            check_cleanup(sid, aid)
    finally:
        llm.chat_raw, llm.enabled, llm.supports_files, llm._post = (ORIG["chat_raw"], ORIG["enabled"],
                                                                    ORIG["supports_files"], ORIG["post"])
        shutil.rmtree(folder, ignore_errors=True)
    if SCEN_REPORT:
        print("\nсценарии (сумма, % страховой суммы):")
        for k, v in SCEN_REPORT.items():
            print("  ", k, v)
    print(f"\nитог: ок {passed}, плохо {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
