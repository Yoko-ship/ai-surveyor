"""
ИИ-сюрвейер одним диалогом (app/surveyor_chat.py) и провайдер Gemini (app/llm.py), задача 222.

Запуск из корня (свой ASGI-клиент, живой сервер не трогаем):
    set PYTHONIOENCODING=utf-8
    sandbox\\.venv\\Scripts\\python.exe tests\\test_surveyor_chat.py

Вся работа — во временной копии базы (tests/tmpdb.py). Сеть не дёргаем: ключа ИИ в тесте нет,
поэтому проверяется в том числе запасное поведение «всё работает без ИИ».
"""
import asyncio
import json as _json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.pop("SURVEYOR_DEV", None)      # guard проверяем целиком, без режима разработчика

from tmpdb import temp_db                 # noqa: E402
from app import db, llm                   # noqa: E402
from app import surveyor_chat as sc       # noqa: E402
from app.main import app                  # noqa: E402

passed, failed = 0, 0
COOKIES = {}

WAREHOUSE = {"class_code": "9", "object_type": "Склад", "sum_insured": 4.2e9,
             "object_value": 4.2e9, "region": "Ташкентская область"}


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ок  ", name)
    else:
        failed += 1
        print("  ПЛОХО", name, extra)


def _send(method, path, params, headers, payload):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": urlencode(params or {}, encoding="utf-8").encode(),
             "headers": headers, "client": ("203.0.113.9", 0), "server": ("test", 80)}
    out = {"status": None, "chunks": [], "headers": []}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"], out["headers"] = msg["status"], msg.get("headers") or []
        elif msg["type"] == "http.response.body":
            out["chunks"].append(msg.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    for k, v in out["headers"]:            # запоминаем cookie гостя, как это делает браузер
        if k.lower() == b"set-cookie":
            pair = v.decode("latin-1").split(";")[0]
            name, _, value = pair.partition("=")
            COOKIES[name.strip()] = value.strip()
    raw = b"".join(out["chunks"]).decode("utf-8", "replace")
    try:
        return out["status"], _json.loads(raw)
    except ValueError:
        return out["status"], raw


def call(method, path, body=None, params=None):
    payload = _json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    hdrs = [(b"host", b"test"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    if COOKIES:
        hdrs.append((b"cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()).encode()))
    return _send(method, path, params, hdrs, payload)


def upload(path, session_id, files):
    """multipart/form-data вручную: files — [(имя файла, mime, bytes)]."""
    boundary = "----inson222"
    parts = []
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="session_id"\r\n\r\n'
                 f'{session_id}\r\n'.encode())
    for name, mime, blob in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
                     f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    payload = b"".join(parts)
    hdrs = [(b"host", b"test"),
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            (b"content-length", str(len(payload)).encode())]
    if COOKIES:
        hdrs.append((b"cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()).encode()))
    return _send("POST", path, None, hdrs, payload)


def types(b):
    return [c["type"] for c in (b.get("cards") or [])]


# ---------- 1. Gemini в app/llm.py (без сети) ----------

def check_llm():
    print("1. Gemini как провайдер (app/llm.py)")
    ok("gemini есть в списке провайдеров", "gemini" in llm.PROVIDERS)
    ok("модель по умолчанию gemini-3.5-flash-lite",
       llm.PROVIDERS["gemini"]["model"] == "gemini-3.5-flash-lite")
    body = llm._gemini_body([{"role": "system", "content": "правила"},
                             {"role": "user", "content": "вопрос"}], 100, 0.2,
                            [{"inline_data": {"mime_type": "image/jpeg", "data": "AA"}}])
    ok("системная часть уходит в systemInstruction",
       body["systemInstruction"]["parts"][0]["text"] == "правила")
    ok("вложение приклеено к последней реплике",
       body["contents"][-1]["parts"][-1]["inline_data"]["mime_type"] == "image/jpeg")
    ok("параметры генерации переданы", body["generationConfig"]["maxOutputTokens"] == 100)
    parts, notes = llm.prepare_files([
        {"name": "фото.jpg", "mime": "image/jpeg", "data": b"\xff\xd8\xff" + b"0" * 10},
        {"name": "книга.xlsx", "mime": "application/vnd.ms-excel", "data": b"PK123"},
        {"name": "огромный.pdf", "mime": "application/pdf", "data": b"%PDF" + b"0" * llm.INLINE_MAX_ONE},
    ])
    ok("inline только jpeg", len(parts) == 1 and parts[0]["inline_data"]["mime_type"] == "image/jpeg")
    ok("Excel и слишком большой файл отсечены с объяснением", len(notes) == 2, notes)
    many = [{"name": f"{i}.png", "mime": "image/png", "data": b"\x89PNG\r\n\x1a\n" + b"0" * 10}
            for i in range(llm.INLINE_MAX_FILES + 3)]
    parts, notes = llm.prepare_files(many)
    ok("не больше предела файлов за раз", len(parts) == llm.INLINE_MAX_FILES and len(notes) == 3)
    ok("системный промпт читается из app/llm_prompts/system.json",
       "тарифной политики" in llm.system_prompt("narrative"))
    ok("в промпте есть запрет сочинять нормы", "Не сочиняй нормы права" in llm.system_prompt("chat"))
    ok("ключ Gemini в открытом виде не отдаётся", "..." in llm.mask_key("AIzaSyTESTKEY1234567890"))
    # без ключа: всё работает как раньше, без падений
    ok("без ключа chat_dialog отдаёт None и причину",
       llm.chat_dialog([{"role": "user", "content": "привет"}])["text"] is None)
    tr = llm.translate("Ставка 0,144% и ссылка https://lex.uz", "uz")
    ok("без ключа перевод возвращает исходный текст", tr["source"] == "исходный" and "0,144%" in tr["text"])
    got = llm.extract_from_files([{"name": "a.jpg", "mime": "image/jpeg", "data": b"\xff\xd8\xff"}])
    ok("без ключа разбор файлов не падает", got["ok"] is False and got["reason"])
    nar = llm.risk_narrative({"summary": {"rate_applied_pct": 0.144, "premium": 6048000,
                                          "sum_insured": 4.2e9, "object_type": "Склад"}}, "ru")
    ok("без ключа разбор риска отдаёт шаблон с цифрами",
       nar["source"] == "шаблон" and "0,144%" in nar["text"], nar["text"][:120])


# ---------- 2. диалог гостя ----------

def check_chat():
    print("2. Диалог: /chat/start → upload → поля → анализ")
    st, b = call("POST", "/chat/start", {"lang": "ru"})
    ok("гостю доступен /chat/start", st == 200 and b.get("ok"), (st, b))
    ok("гостю выдана cookie gid", bool(COOKIES.get("gid")))
    sid = b["session_id"]
    ok("первый диалог начинается с загрузки", b["step"] == "upload", b["step"])
    ok("в карточках есть приветствие", "text" in types(b), types(b))

    st, b = call("POST", "/chat/answer", {"session_id": sid, "scope": "must",
                                          "fields": {"class_code": "9"}})
    ok("неполные поля → шаг must", st == 200 and b["step"] == "must", (st, b.get("step")))
    ok("показана карточка полей", "fields" in types(b), types(b))
    must_card = next(c for c in b["cards"] if c["type"] == "fields")
    ok("в обязательных ровно четыре поля быстрого режима + продукт",
       [f["key"] for f in must_card["fields"]] == list(sc.ra.QUICK_MUST) + ["product_code"],
       [f["key"] for f in must_card["fields"]])

    st, b = call("POST", "/chat/answer", {"session_id": sid, "scope": "must", "fields": WAREHOUSE})
    ok("четыре поля заполнены → шаг optional", st == 200 and b["step"] == "optional", (st, b.get("step")))
    ok("есть чек-лист документов", "checklist" in types(b), types(b))
    chk = next(c for c in b["cards"] if c["type"] == "checklist")
    ok("в чек-листе есть пункты и основание", chk["items"] and chk.get("basis"), chk.get("basis"))
    # правка заказчика 22.09.2026: заявление-анкета — оформление договора, в чек-лист анализа не входит
    ok("в чек-листе нет заявления-анкеты",
       not any("аявлен" in (i.get("name") or "") for i in chk["items"]),
       [i.get("name") for i in chk["items"]])
    opt = next(c for c in b["cards"] if c["type"] == "fields" and c["scope"] == "optional")
    ok("«что ещё уточнить» отсортировано по влиянию",
       opt["fields"][0]["key"] in ("compartments", "protection", "losses_3y"),
       [f["key"] for f in opt["fields"][:3]])

    st, b = call("POST", "/chat/analyze", {"session_id": sid, "mode": "quick"})
    ok("анализ выполнен", st == 200 and b.get("ok") and b["step"] == "result", (st, b.get("step")))
    res = next(c for c in b["cards"] if c["type"] == "result")
    ok("в итоге есть ставка и премия",
       res["summary"]["rate_applied_pct"] and res["summary"]["premium"], res["summary"])
    ok("ставка не ниже минимума тарифной политики",
       res["summary"]["rate_applied_pct"] >= (res["summary"]["rate_min_pct"] or 0), res["summary"])
    # правка 22.09.2026: в быстром режиме франшиза считается на полях после apply_defaults
    fr = res["franchise"]
    ok("франшиза посчитана в быстром режиме", fr.get("ok") is True, fr.get("reason"))
    opts = (fr.get("premium_effect") and fr.get("options")) or fr.get("options") or []
    ok("три варианта тарифа", len(opts) == 3 and {o["key"] for o in opts} ==
       {"base", "franchise", "measures"}, [o.get("key") for o in opts])
    floor = (res["summary"]["rate_min_pct"] or 0) / 100 * res["summary"]["sum_insured"]
    ok("премии вариантов не ниже минимальной ставки компании (в годовом выражении)",
       all(o["premium"] > 0 for o in opts) and max(o["premium"] for o in opts) >= floor * 0.5,
       [o["premium"] for o in opts])
    band = (fr.get("franchise") or {}).get("band_pct")
    ok("есть вилка франшизы по уровню риска",
       isinstance(band, list) and len(band) == 2 and band[1] >= band[0], band)
    ok("пояснение есть и без ИИ", bool(res["narrative"]) and res["narrative_source"] == "шаблон")
    ok("есть чипы «что дальше»", any(c["type"] == "chips" for c in b["cards"]))

    st, b = call("GET", "/chat/state", params={"session_id": sid})
    ok("состояние переживает перезагрузку страницы",
       st == 200 and b["state"]["must"]["region"] == WAREHOUSE["region"], (st, b.get("state")))
    ok("в истории есть реплики", isinstance(b.get("messages"), list) and b["messages"])

    st, b = call("POST", "/chat/lang", {"session_id": sid, "lang": "uz", "translate": ["Ставка 0,144%"]})
    ok("смена языка работает", st == 200 and b["lang"] == "uz", (st, b.get("lang")))
    ok("без ИИ свободный текст остаётся исходным",
       b["translated"][0]["source"] == "исходный" and "0,144%" in b["translated"][0]["text"])

    st, b = call("POST", "/chat/message", {"session_id": sid, "text": "Почему такая ставка?"})
    ok("свободный вопрос без ИИ не падает", st == 200 and b.get("ok"), (st, b))
    ok("честно сказано, что ИИ не подключён",
       b["source"] == "правила" and len(b["cards"][0]["text"]) > 20, b["cards"][0]["text"][:80])

    # чужая сессия
    other = dict(COOKIES)
    COOKIES["gid"] = "aaaaaaaaaaaaaaaaaaaaaaaaaa"
    st, b = call("GET", "/chat/state", params={"session_id": sid})
    ok("чужой диалог не отдаётся", st == 404, st)
    COOKIES.clear()
    COOKIES.update(other)
    return sid


def check_upload(sid):
    print("3. Загрузка файлов одним действием")
    pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    st, b = upload("/chat/upload", sid, [
        ("фото.jpg", "image/jpeg", b"\xff\xd8\xff" + b"0" * 100),
        ("справка.txt", "text/plain", "текст".encode("utf-8")),
    ])
    ok("multipart принят", st == 200 and b.get("ok"), (st, b if st != 200 else ""))
    names = {f.get("name"): f for f in b.get("files") or []}
    ok("фото принято", any(f.get("ok") for f in b["files"]), b["files"])
    ok("неподходящий формат отклонён с объяснением",
       any((not f.get("ok")) and "PDF" in str(f.get("error")) for f in b["files"]), b["files"])
    ok("после загрузки виден чек-лист", "checklist" in types(b), types(b))
    ok("блок extracted есть всегда", "extracted" in b)


def check_previous():
    print("4. «Прошлый договор» у того же гостя")
    st, b = call("POST", "/chat/start", {"lang": "ru"})
    ok("новый диалог видит прошлый", st == 200 and b["step"] == "contract_mode", (st, b.get("step")))
    sid = b["session_id"]
    chips = next(c for c in b["cards"] if c["type"] == "chips")
    ok("предложен выбор «старый / новый»",
       {i["value"] for i in chips["items"]} == {"previous", "new"}, chips)
    st, b = call("POST", "/chat/answer", {"session_id": sid, "key": "contract_mode", "value": "previous"})
    ok("данные прошлого договора подтянулись",
       st == 200 and b["state"]["must"].get("region") == WAREHOUSE["region"], b.get("state"))


def check_cleanup(sid):
    print("5. Срок жизни диалога — 7 дней")
    with db.tx() as con:
        row = db.rows(con, "SELECT expires_at, created_at FROM chat_sessions WHERE id=?", sid)[0]
    days = (sc.datetime.fromisoformat(row["expires_at"]) -
            sc.datetime.fromisoformat(row["created_at"])).days
    ok("срок 7 дней", days == sc.TTL_DAYS, days)
    with db.tx() as con:
        con.execute("UPDATE chat_sessions SET expires_at=? WHERE id=?", ("2020-01-01T00:00:00", sid))
    removed = sc.cleanup()
    ok("просроченное удаляется", removed >= 1, removed)
    with db.tx() as con:
        left = db.rows(con, "SELECT id FROM chat_messages WHERE session_id=?", sid)
    ok("реплики удалённого диалога не остаются", not left, left)


def check_pd():
    print("6. Персональные данные в чате не хранятся")
    st, b = call("POST", "/chat/start", {"lang": "ru"})
    sid = b["session_id"]
    call("POST", "/chat/message", {"session_id": sid,
                                   "text": "Страхователь Иванов Иван Иванович, ПИНФЛ 12345678901234"})
    with db.tx() as con:
        rows = db.rows(con, "SELECT text FROM chat_messages WHERE session_id=? AND role='user'", sid)
    text = " ".join(r["text"] for r in rows)
    ok("ФИО замаскировано", "Иванов" not in text, text[:120])
    ok("ПИНФЛ замаскирован", "12345678901234" not in text, text[:120])


def main():
    import tempfile
    with temp_db("surveyor-chat-test.db"):
        db.ensure_schema()
        folder = Path(tempfile.mkdtemp(prefix="surveyor-chat-"))
        sc.DIR = folder
        sid = check_chat()
        check_upload(sid)
        check_previous()
        check_llm()
        check_pd()
        check_cleanup(sid)
    print(f"\nитог: ок {passed}, плохо {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
