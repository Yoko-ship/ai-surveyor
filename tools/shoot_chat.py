"""
Скриншоты вкладки «ИИ-сюрвейер», лёгкая версия (ТЗ 2.0 от 29.09.2026): «Фото → Проверить → Акт».

Поднимает временный экземпляр сервера (копия базы, рабочая не трогается; как tools/perf_check.Instance, но
запускается этим же скриптом в режиме --serve), запускает headless Edge с отладочным портом и водит страницу
по шагам через CDP (стандартная библиотека, без пакетов). Сети к языковой модели у временного сервера нет,
поэтому ответ POST /act/photos подменяется в браузере сохранённым образцом ответа сервера
sandbox/act_demo_photos.json (+ одно значение с таблички, чтобы было видно расхождение источников).
Дальше всё настоящее: POST /act/make и GET /act/{id}?lang=uz.
Путь: шаг «Фото» пустой → учебный договор sandbox/flow150_contract.docx (разбирается сервером без модели,
настоящий POST /act/photos) → шаг «Проверить» с подставленными суммой, стоимостью, сроком и регионом
«из документа — проверьте» → «Новый акт» → три снимка в списке → «Читаю фото…» → шаг «Проверить»
с распознанным → «Сформировать акт» (без франшизы) → блок «Франшиза» раскрыт, своя 1 % → шаг «Акт»
с применённой франшизой на 390 и 1440 px → узбекский → предложенная франшиза и «Применить» →
«Новый акт» → «Без фото» → тёмная тема и соседние разделы.
Оценка по объявлениям (30.09.2026): на шаге «Проверить» карточка «Оценка по объявлениям» пустая (ссылки поиска —
настоящий GET /act/market/links: сервер только составляет адреса, к olx.uz никто не ходит) → снимок экрана со списком
объявлений sandbox/mk30/listings.png → «Прочитать объявления»: НАСТОЯЩИЙ POST /act/market/shots — сервер
сохраняет снимок, считает и хранит загрузку 24 часа. Подменена только функция обращения к модели
(llm.chat_raw во временном экземпляре): модель недоступна, поэтому она отвечает объявлениями из образца
sandbox/mk30/shots.json в формате ответа модели. Курса ЦБ у временного сервера нет (сети нет) — курс вводит
сотрудник в поле «Курс доллара». Дальше настоящий POST /act/make по живой загрузке снимков: в акте источник
«OLX, объявления на дату, снимки загружены сотрудником». → объявления с отметками и итогом → акт с плиткой
«Оценка по объявлениям» на 390 и 1440 px и на узбекском. Складывает PNG в sandbox/, печатает горизонтальную
прокрутку, служебные слова на экране (undefined, null, calibrated…) и ошибки консоли страницы.
Запрос филиала и договор (30.09.2026):
  • договор — sandbox/flow150_contract.docx, настоящий POST /act/photos (разбирается без модели) → карточка
    «Договор страхования» на шаге 2 → настоящий POST /act/make → плитка «Сверка с договором»;
  • запрос филиала — скан sandbox/br30/25.png, ответ /act/photos подменён сохранённым ответом живого сервера
    sandbox/br30/photos_25.json → карточка «Запрос филиала» (код 0832 выбирает продукт, срок 1888 дн.) → настоящий
    POST /act/make с optional.request → плитка «Сверка с запросом филиала» (тариф ниже минимума, расхождение
    премии +3 870 сум) на 390 и 1440 px и на узбекском; правка премии → source = input;
  • запрос + договор со скана — тот же запрос и договор sandbox/ct30/photos_b2_uzc_scan.json, cross_check собран
    app/contract_read.cross_check → карточки договора со скана и «Запрос и договор: расхождения» → акт с плиткой.
Аналитика риска (30.09.2026): после «Без фото» — два акта настоящим POST /act/make: автокран (продукт 0318) и
оборудование класса 8 по запросу филиала sandbox/br30/sorov_equipment.docx (настоящий POST /act/photos без модели:
вид «оборудование», описание → пищевое производство, 0832, 47 397 852 345,04 сум, 1888 дн.; как в act_demo_equipment) →
все карточки «Аналитики риска» раскрыты → снимки каждой карточки на 390 px, блок и крупные таблицы на 1440 px,
раздел 4 документа с таблицами; оборудование — ещё и на узбекском (chat_an_*.png).
Шаблоны классов и комплексный продукт по частям (30.09.2026, shoot_parts): продукт 0312 (автокредит) — настоящий
POST /act/make: первый расчёт без частей → предложение сервера в карточке «Части договора» (chat_pt_step2_*), расхождение
суммы (кнопка расчёта неактивна), части 60 млн класс 3 и 40 млн класс 14 с полями классов из шаблонов, подтверждение →
шаг 3: плитки договора, таблица частей, раскрытая часть (chat_pt_step3_*), 390 и 1440 px, узбекский; правка сумм без
подтверждения → жёлтая плашка. Поля класса 13 из GET /act/templates/13 на шаге 2 (chat_tpl13_*), ракурсы шага «Фото» из
шаблона класса 3 (chat_tpl_views_390.png), админка /admin/hub#tariffs/templates с ответом 422 (admin_tpl_*).
Страховой скоринг объекта (01.10.2026, shoot_scoring; только он — ключ --scoring): карточка скоринга на шаге «Акт»
у автокрана и оборудования (настоящий POST /act/make) на 390 и 1440 px и на узбекском (chat_sco_*); кредитный продукт
0312 с отчётом кредитного бюро — текстовый PDF на выдуманных данных sandbox/katm_demo.pdf, настоящий POST /act/photos
без модели: подсказка на шаге «Фото», пометка файла, карточка «Отчёт кредитного бюро» с правкой класса (chat_cb_*),
блок заёмщика на карточке скоринга и «Заёмщик: данные кредитного бюро» в акте.

Запуск из корня проекта:
    sandbox\\.venv\\Scripts\\python.exe tools\\shoot_chat.py
    sandbox\\.venv\\Scripts\\python.exe tools\\shoot_chat.py --analytics     (только аналитика риска)
    sandbox\\.venv\\Scripts\\python.exe tools\\shoot_chat.py --parts         (только части, шаблоны классов, админка)
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from perf_check import Instance  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT, DBG = 8123, 9223
OUT = ROOT / "sandbox"
PHOTOS_SAMPLE = ROOT / "sandbox" / "act_demo_photos.json"        # сохранённый образец ответа POST /act/photos
FILES = [ROOT / "sandbox" / "gen" / "test_1.jpg", ROOT / "sandbox" / "gen" / "test_2.jpg",
         ROOT / "sandbox" / "docx_p0.png"]                       # картинки без данных людей
CONTRACT = ROOT / "sandbox" / "flow150_contract.docx"            # учебный договор: разбирается без модели
# образец объявлений (сохранённый ответ POST /act/market/shots): из него собирается ответ подменённой модели;
# сам POST /act/market/shots во время съёмки — настоящий
SHOTS_SAMPLE = ROOT / "sandbox" / "mk30" / "shots.json"
LISTINGS_SHOT = ROOT / "sandbox" / "mk30" / "listings.png"        # снимок экрана со списком объявлений (без продавцов)
USD_RATE = "12650"
BR_SAMPLE = ROOT / "sandbox" / "br30" / "photos_25.json"          # ответ живого сервера на скан запроса филиала
BR_SCAN = ROOT / "sandbox" / "br30" / "25.png"
CT_SAMPLE = ROOT / "sandbox" / "ct30" / "photos_b2_uzc_scan.json"  # ответ живого сервера на скан договора (2 стр.)
CT_SCAN = ROOT / "sandbox" / "ct30" / "b2_uzc_p1.png"                                                # курс, который «вводит сотрудник»: сети к cbu.uz нет

# что не должно попадать на экран: пустые значения и служебные слова сервера
LEAKS = r"""(() => {
  const t = (document.querySelector(".wrap") || document.querySelector("main") || document.body).innerText;
  return ["undefined", "null", "[object Object]", "NaN", "calibrated", "what_if", "«mixed»", "«food»", "«none»"]
    .filter(w => t.indexOf(w) >= 0);
})()"""


# ------------------------------------------------------------------ WebSocket (минимальный клиент)

class WS:
    def __init__(self, url: str):
        host_port, self.path = url.split("//", 1)[1].split("/", 1)
        host, port = host_port.split(":")
        self.s = socket.create_connection((host, int(port)), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET /" + self.path + " HTTP/1.1\r\nHost: " + host_port + "\r\nUpgrade: websocket\r\n"
                        "Connection: Upgrade\r\nSec-WebSocket-Key: " + key +
                        "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        self.id = 0
        self.events = []                       # события CDP: по ним собираем ошибки консоли

    def _read(self, n):
        while len(self.rest) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise IOError("сокет закрыт")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        head = bytes([0x81])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if b0 & 0x0F == 0x1:
                return json.loads(payload.decode())

    def call(self, method, **params):
        self.id += 1
        mid = self.id
        self.send({"id": mid, "method": method, "params": params})
        while True:
            msg = self.recv()
            if "method" in msg:
                self.events.append(msg)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(method + ": " + json.dumps(msg["error"], ensure_ascii=False))
                return msg.get("result") or {}


def js(ws, expr, wait=0.0):
    out = ws.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
    if wait:
        time.sleep(wait)
    res = out.get("result") or {}
    if res.get("subtype") == "error":
        raise RuntimeError(res.get("description"))
    return res.get("value")


def shot(ws, name, width, height=900, cap=4000, scale=2):
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
            deviceScaleFactor=scale, mobile=width < 700)
    time.sleep(0.8)
    metrics = ws.call("Page.getLayoutMetrics")
    full = int(min(cap, metrics["cssContentSize"]["height"]))
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=full,
            deviceScaleFactor=scale, mobile=width < 700)
    time.sleep(0.5)
    data = ws.call("Page.captureScreenshot", format="png")["data"]
    path = OUT / name
    path.write_bytes(base64.b64decode(data))
    over = js(ws, "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    leaks = js(ws, LEAKS)
    print(f"  {name}: {path.stat().st_size // 1024} КБ, горизонтальная прокрутка: {over} px"
          + (f", НА ЭКРАНЕ: {leaks}" if leaks else ""))
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
            deviceScaleFactor=scale, mobile=width < 700)
    return over


def shot_el(ws, name, width, selector, scale=2, pad=10):
    """Снимок одного блока страницы (карточка, раздел акта) — без остальной длинной страницы."""
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=scale, mobile=width < 700)
    time.sleep(0.6)
    full = int(min(12000, ws.call("Page.getLayoutMetrics")["cssContentSize"]["height"]))
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=full, deviceScaleFactor=scale, mobile=width < 700)
    time.sleep(0.6)
    r = js(ws, "(() => { const e = document.querySelector(" + json.dumps(selector) + "); if (!e) return null;"
               " const b = e.getBoundingClientRect(); return [b.left + scrollX, b.top + scrollY, b.width, b.height]; })()")
    over = js(ws, "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if not r:
        print(f"  {name}: блока {selector} на странице нет")
    else:
        x, y, w, h = r
        clip = {"x": max(0, x - pad), "y": max(0, y - pad), "width": min(width, w + 2 * pad), "height": h + 2 * pad, "scale": 1}
        data = ws.call("Page.captureScreenshot", format="png", clip=clip, captureBeyondViewport=True)["data"]
        path = OUT / name
        path.write_bytes(base64.b64decode(data))
        leaks = js(ws, LEAKS)
        print(f"  {name}: {path.stat().st_size // 1024} КБ, горизонтальная прокрутка: {over} px"
              + (f", НА ЭКРАНЕ: {leaks}" if leaks else ""))
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=scale, mobile=width < 700)
    return over


MODEL_KEYS = ("title", "price", "currency", "year", "mileage_km", "hours", "region", "posted", "posted_date", "site",
              "relevant", "why_excluded")


def model_answer() -> str:
    """Ответ «модели» из образца sandbox/mk30/shots.json — в той схеме, что просит act_market.SCHEMA_HINT."""
    d = json.loads(SHOTS_SAMPLE.read_text(encoding="utf-8"))
    rows = [dict({k: r.get(k) for k in MODEL_KEYS}, file=1) for r in d.get("listings") or []]
    return json.dumps({"listings": rows}, ensure_ascii=False)


def serve_fake_model(port: int, copy: Path):
    """
    Временный экземпляр сервера (подпроцесс): всё как tools/perf_check.serve, но модель «подключена» —
    llm.chat_raw отвечает образцом. Загрузка снимков, хранение, курс и /act/make остаются настоящими.
    """
    from perf_check import serve
    from app import llm
    answer = model_answer()

    def chat_raw(purpose, messages, max_tokens=700, temperature=0.2, files=None, timeout=None, retries=None):
        if "объявлени" not in str(purpose):
            return {"text": None, "ok": False, "notes": [], "ms": 0, "reason": "модель недоступна во временном сервере"}
        return {"text": answer, "ok": True, "notes": [], "ms": 1, "reason": None}
    llm.enabled = lambda: True
    llm.supports_files = lambda: True
    llm.chat_raw = chat_raw
    serve(port, copy)


class ShootInstance(Instance):
    """Как perf_check.Instance, но подпроцесс — этот скрипт в режиме --serve (с подменённой моделью)."""

    def __enter__(self):
        import perf_check as pc
        if pc._port_busy(self.port):
            raise SystemExit(f"порт {self.port} занят")
        pc.copy_db(self.db, self.source)
        self.token = pc.add_perf_admin(self.db)
        env = {k: v for k, v in os.environ.items()
               if k not in ("SURVEYOR_DEV", "TG_POLLING", "DEMO_SEED", "STORAGE_DIR")}
        env.update({"STORAGE_DIR": str(self.dir), "PYTHONIOENCODING": "utf-8", "TG_POLLING": "0", "SURVEYOR_DEV": "1"})
        self._logf = open(self.log, "w", encoding="utf-8")
        self.proc = subprocess.Popen([pc.server_python(), str(Path(__file__).resolve()), "--serve", str(self.port),
                                      str(self.db)], cwd=str(ROOT), env=env, stdout=self._logf,
                                     stderr=subprocess.STDOUT)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit("временный экземпляр не поднялся:\n" + self.log.read_text(encoding="utf-8")[-3000:])
            try:
                urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2).read()
                return self
            except OSError:
                time.sleep(0.2)
        raise SystemExit("временный экземпляр не ответил на /health за 90 с")


def console_errors(ws):
    out = []
    for e in ws.events:
        m, p = e.get("method"), e.get("params") or {}
        if m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails") or {}
            out.append("исключение: " + str((d.get("exception") or {}).get("description") or d.get("text"))[:300])
        elif m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
            out.append("console.error: " + " ".join(str(a.get("value") or a.get("description") or "")
                                                    for a in p.get("args") or [])[:300])
        elif m == "Log.entryAdded" and (p.get("entry") or {}).get("level") == "error"                 and "favicon.ico" not in str((p.get("entry") or {}).get("url") or "")                 and not ("422" in str(p["entry"].get("text")) and "/act/templates/" in str(p["entry"].get("url"))):
            # у /admin/hub значка нет; 422 на PUT шаблона — проверка ошибки структуры, её снимок и есть цель
            en = p["entry"]
            out.append("лог: " + str(en.get("text"))[:200] + " " + str(en.get("url") or "")[:120])
    ws.events.clear()
    return out


def set_files(ws, selector, paths):
    """Файл в <input type=file> — как если бы человек выбрал его в «Галерея / файлы»."""
    doc = ws.call("DOM.getDocument", depth=-1)
    node = ws.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=selector)["nodeId"]
    ws.call("DOM.setFileInputFiles", files=[str(x) for x in paths], nodeId=node)


def wait_event(ws, method, timeout=20.0):
    """Ждёт событие CDP (например, Fetch.requestPaused); уже пришедшие берёт из ws.events."""
    end = time.time() + timeout
    while time.time() < end:
        for e in list(ws.events):
            if e.get("method") == method:
                ws.events.remove(e)
                return e.get("params") or {}
        ws.s.settimeout(max(0.1, end - time.time()))
        try:
            msg = ws.recv()
        except (socket.timeout, OSError):
            break
        finally:
            ws.s.settimeout(60)
        if "method" in msg:
            ws.events.append(msg)
    return None


def photos_answer():
    """Ответ /act/photos из образца + значение с таблички, расходящееся с маркировкой (как в ТЗ, п. 8.6)."""
    d = json.loads(PHOTOS_SAMPLE.read_text(encoding="utf-8"))
    # index — номер файла в запросе (по нему страница сопоставляет ответ); третий файл «не поместился в запрос»
    d["files"] = [dict(f, name=p.name, index=i + 1, read_by_ai=i < 2) for i, (f, p) in enumerate(zip(d["files"], FILES))]
    d["not_sent"] = [3]
    d["recognized"].insert(4, {"key": "curb_mass", "label": "Снаряжённая масса", "value": "36170 kg", "source": "plate",
                               "source_label": "с заводской таблички", "note": None, "file": "f3", "check": True,
                               "check_label": "проверьте"})
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def br_answer(with_contract=False):
    """Ответ /act/photos на скан запроса филиала (+ договор со скана и их сверка — как собрал бы сервер)."""
    d = json.loads(BR_SAMPLE.read_text(encoding="utf-8"))
    d["files"] = [dict(d["files"][0], name=BR_SCAN.name, index=1)]
    if with_contract:
        from app import contract_read as cr
        c = json.loads(CT_SAMPLE.read_text(encoding="utf-8"))
        d["files"].append(dict(c["files"][0], id="f2", index=2, name=CT_SCAN.name))
        d["contract"] = dict(c["contract"], file="f2")
        req = dict(d["branch_request"]["fields"], class_hint="equipment")
        xc = cr.cross_check(req, c["contract"]["fields"])
        d["cross_check"] = {"available": True, "items": xc["items"], "differs": xc["differs"], "missing": xc["missing"]}
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def fulfill(ws, paused, body):
    ws.call("Fetch.fulfillRequest", requestId=paused["requestId"], responseCode=200,
            responseHeaders=[{"name": "Content-Type", "value": "application/json; charset=utf-8"}],
            body=base64.b64encode(body).decode())


WAIT_REFS = """(async () => { for (let i = 0; i < 40 && !CH.refs; i++) await new Promise(r => setTimeout(r, 250)); })()"""
LANG_UZ = "document.querySelector('[data-lang-pick=\"uz\"]').click()"
LANG_RU = "document.querySelector('[data-lang-pick=\"ru\"]').click()"


FILL_STEP2 = """(async () => {
  for (let i = 0; i < 40 && !CH.refs; i++) await new Promise(r => setTimeout(r, 250));
  wzPickProduct("0318");
  Object.assign(CH.must, {sum_insured: 2945000000, object_value: 3100000000, region: "tashkent_region"});
  Object.assign(CH.opt, {location: "open_area", losses_count: "0"});
  wzSave();
  wzPaint(true);
})()"""


# оборудование класса 8 — как в примере акта (sandbox/act_demo_equipment.*): запрос филиала DOCX
# sandbox/br30/sorov_equipment.docx (строки бланка 25.png, «Технологик асбоб ускуна нон махсулотлари ишлаб чиқариш
# учун»). Настоящий POST /act/photos разбирает его без модели: вид объекта equipment, описание (→ деятельность
# «пищевое производство»), код 0832, сумма = стоимость 47 397 852 345,04, срок 1888 дней; в POST /act/make уходят
# session, recognized и optional.request — так же, как у сотрудника, загрузившего запрос филиала.
# Без документа вид объекта принимается по умолчанию («производственное здание», 0,35 %) — это другой пример.
BR_EQUIP = ROOT / "sandbox" / "br30" / "sorov_equipment.docx"
FILL_EQUIP = """(async () => {
  for (let i = 0; i < 40 && !CH.refs; i++) await new Promise(r => setTimeout(r, 250));
  if (CH.must.product_code !== "0832") wzPickProduct("0832");
  Object.assign(CH.must, {region: "tashkent_region"});
  wzSave();
  wzPaint(true);
})()"""
EQUIP_INFO = """JSON.stringify({kind: CH.kind, prod: CH.must.product_code, s: CH.must.sum_insured, v: CH.must.object_value,
  term: CH.opt.term_days, rec: CH.rec.filter(r => r.key === "object_type").map(r => r.value), session: !!CH.session})"""


def ensure_equip_request():
    """Запрос филиала для снимков оборудования: если файла нет — собирается тем же построителем, что в тестах."""
    if BR_EQUIP.exists():
        return
    sys.path.insert(0, str(ROOT / "tests"))
    import test_act as T                       # noqa: E402 — только docx_table и строки бланка BR_SAMPLE2
    BR_EQUIP.write_bytes(T.docx_table(T.BR_SAMPLE2))
# все карточки аналитики раскрыты, у первого риска раскрыта строка
AN_OPEN = """(() => { CH.anOpen = {}; AN_CARDS.forEach(k => { CH.anOpen[k] = true; }); wzPaint(true);
  const r = document.querySelector('details.an-rk'); if (r) r.open = true; window.scrollTo(0, 0); })()"""
AN_INFO = """(() => { const a = CH.act, an = a && a.analytics; if (!an) return 'акта нет';
  return [a.number, 'available=' + an.available + (an.reason ? ' (' + an.reason + ')' : ''),
    'рисков ' + ((an.risks || {}).items || []).length, 'факторов ' + ((an.factors || {}).items || []).length,
    'чувствительность ' + ((an.sensitivity || {}).items || []).length, 'тариф ' + (an.tariff || {}).available,
    'база ' + (((an.tariff || {}).rows || [])[0] || {}).label + ' ' + (((an.tariff || {}).rows || [])[0] || {}).value,
    'удержание ' + (an.retention || {}).verdict, 'балл ' + (an.score || {}).score,
    'рынок ' + (an.market || {}).rate_pct, 'показателей ' + ((an.stats || {}).indicators || []).length,
    'франшиза ' + ((an.franchise || {}).rows || []).length, 'таблиц в документе ' +
    a.sections.reduce((n, s) => n + (s.lists || []).filter(l => l.table).length, 0)].join(' | '); })()"""


def shoot_analytics(ws, bad):
    """
    Шаг 3 с раскрытой аналитикой риска (30.09.2026): автокран (продукт 0318) и оборудование класса 8 (0832,
    47 397 852 345,04 сум, 1888 дней). Акты — настоящим POST /act/make временного сервера; карточки по одной
    на 390 px, блок целиком и крупные таблицы на 1440 px, раздел 4 документа с таблицами; оборудование — на узбекском.
    """
    ensure_equip_request()
    try:
        ws.call("Fetch.disable")          # запрос филиала оборудования — настоящий POST /act/photos, без подмены
    except RuntimeError:
        pass
    for key, fill in (("crane", FILL_STEP2), ("equip", FILL_EQUIP)):
        js(ws, "actReset()", 1)
        if key == "equip":
            # запрос филиала: «Дальше» — POST /act/photos (DOCX разбирается сервером без модели) → шаг 2
            set_files(ws, "#chatFile", [BR_EQUIP])
            time.sleep(1)
            js(ws, 'document.querySelector("#chatMain").click()', 5)
            print("  оборудование, запрос филиала:", js(ws, EQUIP_INFO), "| ошибка:", js(ws, "CH.err") or "нет")
        else:
            js(ws, 'document.querySelector("#chatMain").click()', 1)      # «Без фото» → шаг 2
        js(ws, fill, 2)
        if key == "crane":
            js(ws, 'Object.assign(CH.opt, {location: "open_area", losses_count: "0"}); wzSave()', 0.2)
        js(ws, 'document.querySelector("#chatMain").click()', 0.5)        # «Сформировать акт»
        for _ in range(80):
            if js(ws, "!CH.busy && (CH.wz === 3 && !!CH.act || !!CH.err)"):
                break
            time.sleep(0.25)
        time.sleep(1)
        print(f"  аналитика ({key}): шаг", js(ws, "CH.wz"), "| ошибка:", js(ws, "CH.err") or "нет")
        print("   ", js(ws, AN_INFO))
        js(ws, AN_OPEN, 1)
        cards = js(ws, "Array.from(document.querySelectorAll('details.an-c')).map(d => d.dataset.an)") or []
        for c in cards:
            bad.append(shot_el(ws, f"chat_an_{key}_{c}_390.png", 390, f'details.an-c[data-an="{c}"]'))
        bad.append(shot_el(ws, f"chat_an_{key}_1440.png", 1440, ".an-box", scale=1))
        for c in ("risks", "factors", "scen", "score", "market"):
            bad.append(shot_el(ws, f"chat_an_{key}_{c}_1440.png", 1440, f'details.an-c[data-an="{c}"]', scale=1))
        js(ws, "document.querySelectorAll('details.act-s').forEach(d => { d.open = d.dataset.sn === '4'; })", 0.3)
        bad.append(shot_el(ws, f"chat_an_{key}_doc4_390.png", 390, 'details.act-s[data-sn="4"]'))
        bad.append(shot_el(ws, f"chat_an_{key}_doc4_1440.png", 1440, 'details.act-s[data-sn="4"]', scale=1))
    # узбекский: GET /act/{id}?lang=uz, карточки перерисованы на новом языке и остаются раскрытыми
    js(ws, LANG_UZ, 5)
    print("  аналитика на узбекском: язык акта", js(ws, "CH.act && CH.act.lang"))
    js(ws, AN_OPEN, 1)
    for c in ("sum", "risks", "factors", "sens", "ret", "market"):
        bad.append(shot_el(ws, f"chat_an_equip_{c}_uz_390.png", 390, f'details.an-c[data-an="{c}"]'))
    bad.append(shot_el(ws, "chat_an_equip_uz_1440.png", 1440, ".an-box", scale=1))
    js(ws, "document.querySelectorAll('details.act-s').forEach(d => { d.open = d.dataset.sn === '4'; })", 0.3)
    bad.append(shot_el(ws, "chat_an_equip_doc4_uz_390.png", 390, 'details.act-s[data-sn="4"]'))
    js(ws, LANG_RU, 4)
    js(ws, "actReset()", 1)


# ------------------------------------------------------------------ страховой скоринг объекта (01.10.2026)

# отчёт кредитного бюро на выдуманных данных (как tests/test_act.py, проверки 43г): юрлицо, класс D1, просрочка —
# PDF с текстом, сервер разбирает его правилами без модели; телефон и адрес в отчёте есть, но не извлекаются
KATM_DEMO = ROOT / "sandbox" / "katm_demo.pdf"
KATM_LINES = [
    "Кредитное бюро «Кредитно-информационный аналитический центр»",
    "Тип кредитного отчёта: InfoScore",
    "Номер запроса: 1234567890   Время запроса: {date} 10:15:00",
    "1. СУБЪЕКТ КРЕДИТНОЙ ИНФОРМАЦИИ",
    'Наименование: ООО "SINOV SAVDO"',
    "Юридический статус: Юридическое лицо",
    "ИНН: 301234567",
    "ОКЭД: 47190",
    "Адрес регистрации: г. Тестовый, ул. Примерная, 1",
    "Номер телефона: 998901234567",
    "2. SCORING",
    "СКОРИНГОВЫЙ БАЛЛ: 180",
    "КЛАСС ОЦЕНКИ: D1, СЛАБЫЙ уровень",
    "ВЕРСИЯ СКОРИНГА: 3.0",
    "3. ОБЩИЙ ОБЗОР (ОТКРЫТЫЕ + ЗАКРЫТЫЕ)",
    "5 - заявки",
    "4 - договора",
    "1 - условные обязательства",
    "8 - запросы и подписки по субъекту КИ",
    "12 500 000 - среднемесячный платёж (сумма)",
    "2 - количество просрочек основного долга (ОД)",
    "15 - максимальная просрочка ОД (дни)",
    "3 000 000 - максимальная просрочка ОД (сумма)",
    "4 - максимальная непрерывная просрочка % (дни)",
    "250 000 - всего просроченных % (сумма)",
    "4. ДЕЙСТВУЮЩИЕ ДОГОВОРА",
    '1 АКБ "NAMUNA BANK" 100200300400 UZS 150 000 000.00 1 200 000.00 8 000 000.00',
    '2 "SINOV BANK" АТБ 100200300401 UZS 50 000 000.00 0 4 500 000.00',
    "Итого 200 000 000.00 1 200 000.00 12 500 000.00",
    "5. ЗАЯВКИ БЕЗ ДОГОВОРОВ",
]


def make_katm_pdf():
    """Текстовый PDF отчёта бюро (выдуманные данные; дата — 5 дней назад, чтобы отчёт был свежим)."""
    import pymupdf
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=5)).isoformat()
    doc = pymupdf.open()
    page = doc.new_page()
    font = pymupdf.Font(fontfile=r"C:\Windows\Fonts\arial.ttf")
    tw = pymupdf.TextWriter(page.rect)
    for i, ln in enumerate(KATM_LINES):
        tw.append((40, 50 + i * 15), ln.format(date=d), font=font, fontsize=9)
    tw.write_text(page)
    KATM_DEMO.write_bytes(doc.tobytes())


SCO_INFO = """(() => { const s = CH.act && CH.act.scoring; if (!s) return 'скоринга нет';
  const n = document.querySelector('#scoCard .sco-needle');
  return [CH.act.number, 'available=' + s.available, 'балл ' + s.score, s.class_code + ' ' + s.class_label,
    'стрелка ' + (n && n.dataset.deg) + '°', 'составляющих ' + (s.components || []).length,
    'частей ' + (s.parts || []).length, 'заёмщик ' + !!s.borrower, 'сводка свёрнута ' + !(document.querySelector('#actSumD') || {}).open,
    'язык ' + CH.act.lang].join(' | '); })()"""


def shoot_scoring(ws, bad):
    """
    Карточка «Страховой скоринг объекта» на шаге «Акт» (01.10.2026): автокран (0318) и оборудование класса 8 (0832) —
    настоящий POST /act/make временного сервера, 390 и 1440 px, узбекский; кредитный продукт 0312 с отчётом бюро
    (текстовый PDF на выдуманных данных, настоящий POST /act/photos без модели) — подсказка на шаге «Фото», карточка
    «Отчёт кредитного бюро» на шаге 2 с правкой класса, блок заёмщика на карточке скоринга и «Заёмщик: данные
    кредитного бюро» в акте.
    """
    ensure_equip_request()
    try:
        ws.call("Fetch.disable")
    except RuntimeError:
        pass
    for key, fill in (("crane", FILL_STEP2), ("equip", FILL_EQUIP)):
        js(ws, "actReset()", 1)
        if key == "equip":
            set_files(ws, "#chatFile", [BR_EQUIP])
            time.sleep(1)
            js(ws, 'document.querySelector("#chatMain").click()', 5)
        else:
            js(ws, 'document.querySelector("#chatMain").click()', 1)      # «Без фото» → шаг 2
        js(ws, fill, 2)
        if key == "crane":
            js(ws, 'Object.assign(CH.opt, {location: "open_area", losses_count: "0"}); wzSave()', 0.2)
        js(ws, 'document.querySelector("#chatMain").click()', 0.5)        # «Сформировать акт»
        wait_js(ws, "!CH.busy && (CH.wz === 3 && !!CH.act || !!CH.err)", 40)
        time.sleep(1)
        print(f"  скоринг ({key}):", js(ws, SCO_INFO), "| ошибка:", js(ws, "CH.err") or "нет")
        js(ws, "window.scrollTo(0, 0)", 0.3)
        bad.append(shot_el(ws, f"chat_sco_{key}_390.png", 390, "#scoCard"))
        bad.append(shot_el(ws, f"chat_sco_{key}_1440.png", 1440, "#scoCard", scale=1))
        if key == "crane":
            bad.append(shot(ws, "chat_sco_step3_390.png", 390, cap=6000))
            # пример «отказать»: решение подменено на экране (правила акта не трогаются) — плашка перечёркнута
            js(ws, "(() => { const s = CH.act.scoring; CH.scoKeep = s.decision; s.decision = {code: 'decline', "
                   "text: 'отказать', title: 'Рекомендация акта', warning: 'см. рекомендацию акта: отказать'}; "
                   "wzPaint(true); })()", 0.6)
            bad.append(shot_el(ws, "chat_sco_decline_390.png", 390, "#scoCard"))
            js(ws, "CH.act.scoring.decision = CH.scoKeep; wzPaint(true)", 0.4)
            # открыть подсказку «как посчитано» у первой составляющей и сводку — сводка раскрывается, кнопки на месте
            js(ws, "document.querySelector('#scoCard details.sco-c').open = true; document.querySelector('#actSumD').open = true", 0.4)
            bad.append(shot_el(ws, "chat_sco_sum_open_390.png", 390, "#actSumD"))
            js(ws, "document.querySelector('#actSumD').open = false", 0.2)
    # узбекский: GET /act/{id}?lang=uz — карточка на новом языке (оборудование)
    js(ws, LANG_UZ, 1)
    wait_js(ws, "!!CH.act && CH.act.lang === 'uz' && !!document.querySelector('#scoCard')", 20)
    time.sleep(1)
    print("  скоринг на узбекском:", js(ws, SCO_INFO))
    bad.append(shot_el(ws, "chat_sco_equip_uz_390.png", 390, "#scoCard"))
    bad.append(shot_el(ws, "chat_sco_equip_uz_1440.png", 1440, "#scoCard", scale=1))
    js(ws, LANG_RU, 1)
    wait_js(ws, "!!CH.act && CH.act.lang === 'ru'", 20)

    # кредитный продукт 0312 с отчётом бюро
    make_katm_pdf()
    js(ws, "actReset()", 1)
    js(ws, WAIT_REFS)
    js(ws, 'CH.must.product_code = "0312"; CH.must.class_code = wzClassesOf(wzProd("0312"))[0] || ""; wzSave(); wzPaint(true)', 1)
    print("  шаг «Фото», подсказка про отчёт бюро:", js(ws, "!!document.querySelector('.cb-hint')"))
    bad.append(shot_el(ws, "chat_cb_step1_390.png", 390, ".cb-hint", pad=24))
    set_files(ws, "#chatFile", [KATM_DEMO])
    time.sleep(1)
    # пометка «это отчёт бюро» у файла (уходит полем kinds; PDF с текстом читается правилами и с пометкой)
    js(ws, 'document.querySelector("[data-cbk]").click()', 0.5)
    print("  пометка «отчёт бюро»:", js(ws, "JSON.stringify(CH.queue.map(q => !!q.isCb))"))
    bad.append(shot_el(ws, "chat_cb_mark_390.png", 390, ".wz-files"))
    js(ws, 'document.querySelector("#chatMain").click()', 0.5)            # настоящий POST /act/photos
    wait_js(ws, "!CH.busy && CH.wz === 2", 30)
    time.sleep(1)
    print("  отчёт бюро на шаге 2:", js(ws, "JSON.stringify(CH.cb && {src: CH.cb.source, score: CH.cb.fields.score, "
                                         "cls: CH.cb.fields.score_class, type: CH.cb.fields.subject_type, od: CH.cb.fields.active.overdue})"),
          "| файл:", js(ws, "(document.querySelector('.wz-file small') || {}).innerText"), "| ошибка:", js(ws, "CH.err") or "нет")
    js(ws, "wzGo(1)", 0.8)
    bad.append(shot_el(ws, "chat_cb_file_390.png", 390, ".wz-files"))
    js(ws, "wzGo(2)", 1)
    type_in(ws, "#cbf-score_class", "c1")                                # правка сотрудника: D1 → C1 (кириллица → латиница)
    print("  правка класса:", js(ws, "CH.cb.fields.score_class"), "| в /act/make:",
          js(ws, "JSON.stringify(actBody().optional.credit_report && {cls: actBody().optional.credit_report.score_class,"
                 " src: actBody().optional.credit_report.source})"))
    js(ws, "document.querySelector('#cbCard').scrollIntoView({block: 'start'})", 0.3)
    bad.append(shot_el(ws, "chat_cb_step2_390.png", 390, "#cbCard"))
    bad.append(shot_el(ws, "chat_cb_step2_1440.png", 1440, "#cbCard", scale=1))
    # суммы и регион; первый расчёт — предложение частей, затем части 60 млн (класс 3) и 40 млн (класс 14)
    js(ws, """(() => { Object.assign(CH.must, {sum_insured: 100000000, object_value: 60000000, region: "tashkent_city"});
      wzSave(); wzPaint(true); })()""", 1)
    js(ws, 'document.querySelector("#chatMain").click()', 0.5)
    wait_js(ws, MAKE_WAIT, 40)
    time.sleep(1.5)
    type_in(ws, "#pts-0", "60 000 000")
    type_in(ws, "#pts-1", "40 000 000")
    wait_js(ws, "!!tplOf('3') && !!tplOf('14')", 15)
    js(ws, "wzPart('parts')", 0.5)
    type_in(ws, "#tf-1-credit_amount", "100 000 000")
    type_in(ws, "#tf-1-collateral_value", "60 000 000")
    js(ws, 'document.querySelector("[data-go=ptconfirm]").click()', 0.8)
    js(ws, 'document.querySelector("#chatMain").click()', 0.5)
    wait_js(ws, "!CH.busy && CH.wz === 3 && !!CH.act && CH.act.parts && CH.act.parts.confirmed", 40)
    time.sleep(1)
    print("  0312 с отчётом бюро:", js(ws, SCO_INFO), "| ошибка:", js(ws, "CH.err") or "нет")
    print("   заёмщик:", js(ws, "JSON.stringify(CH.act.borrower && {src: CH.act.borrower.source_kind, edits: CH.act.borrower.edits,"
                           " checks: CH.act.borrower.check_codes})"))
    js(ws, "window.scrollTo(0, 0)", 0.3)
    bad.append(shot_el(ws, "chat_sco_credit_390.png", 390, "#scoCard"))
    bad.append(shot_el(ws, "chat_sco_credit_1440.png", 1440, "#scoCard", scale=1))
    js(ws, "CH.cbActOpen = true; wzPaint(true)", 1)
    bad.append(shot_el(ws, "chat_cb_act_390.png", 390, "#cbAct"))
    bad.append(shot_el(ws, "chat_cb_act_1440.png", 1440, "#cbAct", scale=1))
    js(ws, "actReset()", 1)


def wait_js(ws, expr, timeout=20.0, step=0.25):
    """Ждёт, пока выражение страницы станет истинным (ответ сервера, шаблон класса)."""
    end = time.time() + timeout
    while time.time() < end:
        if js(ws, expr):
            return True
        time.sleep(step)
    return False


def type_in(ws, selector, value):
    """Ввод в поле так, как печатает человек: значение и событие input (форма не перерисовывается)."""
    js(ws, "(() => { const el = document.querySelector(" + json.dumps(selector) + "); if (!el) return false;"
           " el.focus(); el.value = " + json.dumps(value) + "; el.dispatchEvent(new Event('input', {bubbles: true}));"
           " el.dispatchEvent(new Event('change', {bubbles: true})); el.blur(); return true; })()", 0.3)


MAKE_WAIT = "!CH.busy && (CH.wz === 3 && !!CH.act || !!CH.err || (CH.pt && CH.pt.hint))"
PT_INFO = """(() => { const a = CH.act, P = a && a.parts; return JSON.stringify({wz: CH.wz, err: CH.err || null,
  pt: CH.pt && CH.pt.items.map(x => x.cls + ':' + x.sum), confirmed: CH.pt && CH.pt.confirmed, hint: CH.pt && CH.pt.hint,
  act: P && {mode: P.mode, src: P.source, confirmed: P.confirmed, obj: P.object_mode, prem: P.totals && P.totals.premium,
    rows: P.table && P.table.rows.length, items: (P.items || []).map(p => p.class_code + ':' + p.sum_insured + ':' + p.level
      + ':miss=' + (p.missing || []).length + ':an=' + (p.analytics || {}).available)}}); })()"""


def shoot_parts(ws, bad, port=PORT, token=None):
    """
    Комплексный продукт 0312 (автокредит) по частям — настоящий POST /act/make временного сервера (30.09.2026):
    шаг 2 с карточкой «Части договора» (до расчёта, предложение сервера после первого расчёта, расхождение суммы,
    после подтверждения: 60 млн класс 3 и 40 млн класс 14, как sandbox/act_demo_multi.json) → шаг 3 с плитками
    договора, таблицей частей и раскрытой частью на 390 и 1440 px → узбекский. Затем поля класса 13 из шаблона
    на шаге 2 и админская вкладка «Шаблоны классов» (/admin/hub#tariffs/templates).
    """
    try:
        ws.call("Fetch.disable")
    except RuntimeError:
        pass
    js(ws, "actReset()", 1)
    js(ws, 'document.querySelector("#chatMain").click()', 1)          # «Без фото» → шаг 2
    js(ws, WAIT_REFS)
    js(ws, """(() => { wzPickProduct("0312"); Object.assign(CH.must, {sum_insured: 100000000, object_value: 60000000,
      region: "tashkent_city"}); wzSave(); wzPaint(true); })()""", 1.5)
    js(ws, "document.querySelector('#ptCard').scrollIntoView({block: 'start'})", 0.3)
    bad.append(shot_el(ws, "chat_pt_step2_empty_390.png", 390, "#ptCard"))
    # первый расчёт без частей: сервер предлагает распределение (поровну по умолчанию) → назад на шаг 2 с подсказкой
    js(ws, 'document.querySelector("#chatMain").click()', 0.5)
    wait_js(ws, MAKE_WAIT, 40)
    time.sleep(1.5)
    print("  0312, первый расчёт:", js(ws, PT_INFO))
    bad.append(shot_el(ws, "chat_pt_step2_suggest_390.png", 390, "#ptCard"))
    bad.append(shot_el(ws, "chat_pt_step2_suggest_1440.png", 1440, "#ptCard", scale=1))
    # сотрудник исправляет суммы: сначала только часть 1 — сумма частей расходится, кнопка расчёта неактивна
    type_in(ws, "#pts-0", "60 000 000")
    print("  расхождение: кнопка «Сформировать акт» неактивна =", js(ws, "document.querySelector('#chatMain').disabled"),
          "| итог:", js(ws, "document.querySelector('#ptTotal').innerText"))
    bad.append(shot_el(ws, "chat_pt_step2_diff_390.png", 390, "#ptCard"))
    type_in(ws, "#pts-1", "40 000 000")
    wait_js(ws, "!!tplOf('3') && !!tplOf('14')", 15)
    js(ws, "wzPart('parts')", 0.5)
    js(ws, """(() => { const k = document.querySelector('#tf-0-__kind'); if (k) { k.value = 'car'; k.dispatchEvent(new Event('change', {bubbles: true})); } })()""", 0.3)
    type_in(ws, "#tf-0-brand", "Chevrolet")
    type_in(ws, "#tf-0-year", "2021")
    type_in(ws, "#tf-1-credit_amount", "100 000 000")
    type_in(ws, "#tf-1-collateral_value", "60 000 000")
    type_in(ws, "#tf-1-credit_term_months", "36")
    js(ws, 'document.querySelector("[data-ptsame=diff]").click()', 0.3)
    js(ws, 'document.querySelector("[data-go=ptconfirm]").click()', 0.8)
    print("  после подтверждения: кнопка расчёта неактивна =", js(ws, "document.querySelector('#chatMain').disabled"),
          "| в /act/make:", js(ws, "JSON.stringify({parts: actBody().optional.parts, conf: actBody().optional.parts_confirmed,"
                                  " same: actBody().optional.same_object})"))
    bad.append(shot_el(ws, "chat_pt_step2_confirmed_390.png", 390, "#ptCard"))
    bad.append(shot_el(ws, "chat_pt_step2_confirmed_1440.png", 1440, "#ptCard", scale=1))
    js(ws, 'document.querySelector("#chatMain").click()', 0.5)
    wait_js(ws, "!CH.busy && CH.wz === 3 && !!CH.act && CH.act.parts && CH.act.parts.confirmed", 40)
    time.sleep(1)
    print("  0312, подтверждённые части:", js(ws, PT_INFO))
    js(ws, "window.scrollTo(0, 0)", 0.3)
    bad.append(shot_el(ws, "chat_pt_step3_sum_390.png", 390, ".act-sum"))
    bad.append(shot_el(ws, "chat_pt_step3_sum_1440.png", 1440, ".act-sum", scale=1))
    # раскрытая часть 2 (кредиты): уровень, тариф, франшиза, сценарии, сумма к стоимости, аналитика
    js(ws, "CH.ptOpen = {1: true, 2: true}; CH.anOpen['p2:risks'] = true; CH.anOpen['p2:tariff'] = true; wzPaint(true)", 1)
    bad.append(shot_el(ws, "chat_pt_step3_parts_390.png", 390, ".pt-act"))
    bad.append(shot_el(ws, "chat_pt_step3_part2_390.png", 390, 'details.pt-c[data-ptn="2"]'))
    bad.append(shot_el(ws, "chat_pt_step3_parts_1440.png", 1440, ".pt-act", scale=1))
    bad.append(shot(ws, "chat_pt_step3_full_1440.png", 1440, cap=9000, scale=1))
    # узбекский: акт — GET /act/{id}?lang=uz, шаблоны частей запрашиваются заново на узбекском
    js(ws, LANG_UZ, 5)
    print("  узбекский: язык акта", js(ws, "CH.act && CH.act.lang"), "| источник частей:",
          js(ws, "CH.act && CH.act.parts && CH.act.parts.source_label"))
    js(ws, "CH.ptOpen = {1: true}; wzPaint(true); window.scrollTo(0, 0)", 1)
    bad.append(shot_el(ws, "chat_pt_step3_sum_uz_390.png", 390, ".act-sum"))
    bad.append(shot_el(ws, "chat_pt_step3_parts_uz_390.png", 390, ".pt-act"))
    bad.append(shot_el(ws, "chat_pt_step3_parts_uz_1440.png", 1440, ".pt-act", scale=1))
    js(ws, "wzGo(2)", 1)
    wait_js(ws, "!!tplOf('3') && !!tplOf('14')", 15)
    js(ws, "wzPart('parts')", 0.5)
    bad.append(shot_el(ws, "chat_pt_step2_uz_390.png", 390, "#ptCard"))
    bad.append(shot_el(ws, "chat_pt_step2_uz_1440.png", 1440, "#ptCard", scale=1))
    # «Распределение не подтверждено»: правка суммы снимает подтверждение, акт по правке — с жёлтой плашкой
    js(ws, LANG_RU, 4)
    js(ws, "wzGo(2)", 1)
    type_in(ws, "#pts-0", "70 000 000")
    type_in(ws, "#pts-1", "30 000 000")
    js(ws, 'document.querySelector("#chatMain").click()', 0.5)
    wait_js(ws, "!CH.busy && CH.wz === 3 && !!CH.act && CH.act.parts && !CH.act.parts.confirmed", 40)
    time.sleep(1)
    js(ws, "window.scrollTo(0, 0)", 0.3)
    bad.append(shot_el(ws, "chat_pt_step3_unconfirmed_390.png", 390, ".act-sum"))

    # поля класса 13 (общая ответственность) из шаблона на шаге 2
    js(ws, "actReset()", 1)
    js(ws, 'document.querySelector("#chatMain").click()', 1)
    js(ws, 'wzPickClass("13"); Object.assign(CH.must, {sum_insured: 500000000, object_value: 500000000, region: "tashkent_city"}); wzSave()', 0.3)
    wait_js(ws, "!!tplOf('13')", 15)
    js(ws, "wzPaint(true)", 1)
    js(ws, """(() => { const s = document.querySelector('#tf-m-activity_kind'); if (s) { s.value = 'trade'; s.dispatchEvent(new Event('change', {bubbles: true})); } })()""", 0.3)
    type_in(ws, "#tf-m-limit_per_case", "200 000 000")
    bad.append(shot_el(ws, "chat_tpl13_step2_390.png", 390, "#tplCard"))
    bad.append(shot_el(ws, "chat_tpl13_step2_1440.png", 1440, "#tplCard", scale=1))
    js(ws, 'CH.optOpen = true; wzPart("more")', 0.5)
    bad.append(shot_el(ws, "chat_tpl13_more_390.png", 390, "#wzMore"))
    print("  класс 13 в /act/make:", js(ws, "JSON.stringify({kind: actBody().optional.object_kind, cf: actBody().optional.class_fields})"))
    js(ws, 'document.querySelector("#chatMain").click()', 0.5)
    wait_js(ws, MAKE_WAIT, 40)
    print("  акт класса 13: шаг", js(ws, "CH.wz"), "| ошибка:", js(ws, "CH.err") or "нет",
          "| поля класса в акте:", js(ws, "CH.act && JSON.stringify(CH.act.template && CH.act.template.class_fields)"))
    bad.append(shot_el(ws, "chat_tpl13_docs_390.png", 390, "#wzBody .act-card:nth-last-of-type(2)"))
    # шаг «Фото»: ракурсы и виды объекта — из шаблона класса 3 (продукт 0318)
    js(ws, "actReset()", 1)
    js(ws, 'wzPickProduct("0318"); wzGo(1)', 0.3)
    wait_js(ws, "!!tplOf('3')", 15)
    js(ws, 'wzPart("views")', 0.5)
    bad.append(shot_el(ws, "chat_tpl_views_390.png", 390, "#wzViews"))
    js(ws, "actReset()", 1)
    errs = console_errors(ws)

    # админка: вкладка «Шаблоны классов» (список, класс 3, правка JSON, история)
    if token:                              # тестовый администратор копии базы: PUT шаблона открыт только ему
        ws.call("Network.enable")
        ws.call("Network.setCookie", name="sid", value=token, url=f"http://127.0.0.1:{port}/", path="/")
    ws.call("Page.navigate", url=f"http://127.0.0.1:{port}/admin/hub#tariffs/templates")
    time.sleep(5)
    js(ws, "(() => { const b = document.querySelector('[data-tcls=\"3\"]'); if (b) b.click(); })()", 3)
    bad.append(shot(ws, "admin_tpl_1440.png", 1440, cap=5000, scale=1))
    # ошибка структуры: доли рисков не 100 — сервер отвечает 422, ошибки под кнопкой
    js(ws, """(() => { const t = document.querySelector('#tplRaw'); const o = JSON.parse(t.value);
      if (o.risks && o.risks.items && o.risks.items[0]) o.risks.items[0].share_pct += 10; t.value = JSON.stringify(o, null, 2);
      document.querySelector('[data-tsave]').click(); })()""", 3)
    print("  админка, правка с ошибкой:", js(ws, "(document.querySelector('#tplMsg') || {}).innerText"))
    js(ws, "document.querySelector('#tplMsg').scrollIntoView({block: 'center'})", 0.3)
    bad.append(shot_el(ws, "admin_tpl_422_1440.png", 1440, "#tplOne .card:nth-child(2)", scale=1))
    js(ws, "(() => { const b = document.querySelector('[data-tlang=\"uz\"]'); if (b) b.click(); })()", 3)
    bad.append(shot(ws, "admin_tpl_uz_390.png", 390, cap=4000))
    return errs + console_errors(ws)


def main(only_analytics=False, only_parts=False, only_scoring=False):
    OUT.mkdir(exist_ok=True)
    with ShootInstance(PORT, "dev", keep=False) as inst:
        profile = tempfile.mkdtemp(prefix="edge-shoot-")
        edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={DBG}",
                                 f"--user-data-dir={profile}", "--no-first-run", "--disable-gpu",
                                 "--window-size=390,900", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            url = None
            for _ in range(60):
                try:
                    tabs = json.loads(urlopen(f"http://127.0.0.1:{DBG}/json", timeout=2).read().decode())
                    page = [t for t in tabs if t["type"] == "page"]
                    if page:
                        url = page[0]["webSocketDebuggerUrl"]
                        break
                except Exception:
                    time.sleep(0.4)
            if not url:
                raise SystemExit("Edge не открыл отладочный порт")
            ws = WS(url)
            for dom in ("Page", "Runtime", "Log", "DOM"):
                ws.call(dom + ".enable")
            ws.call("Emulation.setDeviceMetricsOverride", width=390, height=900, deviceScaleFactor=2, mobile=True)
            ws.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/tg")
            time.sleep(7)
            bad = []
            if only_parts:
                errs = shoot_parts(ws, bad, token=inst.token)
                print("итог: горизонтальной прокрутки нет" if not any(x > 0 for x in bad)
                      else "ВНИМАНИЕ: где-то есть горизонтальная прокрутка: " + str(bad))
                print("ошибок в консоли нет" if not errs else "ОШИБКИ В КОНСОЛИ:\n  " + "\n  ".join(errs))
                return
            if only_scoring:
                shoot_scoring(ws, bad)
                errs = console_errors(ws)
                print("итог: горизонтальной прокрутки нет" if not any(x > 0 for x in bad)
                      else "ВНИМАНИЕ: где-то есть горизонтальная прокрутка: " + str(bad))
                print("ошибок в консоли нет" if not errs else "ОШИБКИ В КОНСОЛИ:" + chr(10) + "  " + (chr(10) + "  ").join(errs))
                return
            if only_analytics:
                shoot_analytics(ws, bad)
                errs = console_errors(ws)
                print("итог: горизонтальной прокрутки нет" if not any(x > 0 for x in bad)
                      else "ВНИМАНИЕ: где-то есть горизонтальная прокрутка: " + str(bad))
                print("ошибок в консоли нет" if not errs else "ОШИБКИ В КОНСОЛИ:\n  " + "\n  ".join(errs))
                return
            # шаг 1 «Фото»: пусто — внизу «Без фото», подсказка ракурсов по всем видам объектов
            bad.append(shot(ws, "chat_step1_390.png", 390))

            # учебный договор DOCX: настоящий POST /act/photos, сервер разбирает его без модели и сети
            set_files(ws, "#chatFile", [CONTRACT])
            time.sleep(1)
            js(ws, 'document.querySelector("#chatMain").click()', 5)
            print("  договор: шаг", js(ws, "CH.wz"), "| prefill:", js(ws, "JSON.stringify({s: CH.must.sum_insured, v: CH.must.object_value, "
                  "r: CH.must.region, t: CH.opt.term_days, pre: Object.keys(CH.pre)})"),
                  "| документы:", js(ws, "JSON.stringify(CH.docs)"), "| ошибка:", js(ws, "CH.err") or "нет")
            print("  карточка договора:", js(ws, "JSON.stringify(CH.ct && {src: CH.ct.source, found: CH.ct.found_n, missing: CH.ct.missing,"
                                              " ess: CH.ct.essentials.filter(e => !e.present).map(e => e.code)})"))
            js(ws, "window.scrollTo(0, 0)", 0.3)
            bad.append(shot(ws, "chat_step2_doc_390.png", 390, cap=5000))
            bad.append(shot_el(ws, "chat_step2_ct_390.png", 390, "#ctCard"))
            js(ws, "wzGo(1)", 1)
            bad.append(shot(ws, "chat_step1_doc_390.png", 390, cap=2600))
            # договор → акт: настоящий POST /act/make с optional.contract, плитка «Сверка с договором»
            js(ws, "wzGo(2)", 1)
            js(ws, WAIT_REFS)
            js(ws, 'wzPickProduct("0808"); wzPaint(true)', 1)
            print("  в /act/make уйдёт договор:", js(ws, "JSON.stringify(actBody().optional.contract)"))
            js(ws, 'document.querySelector("#chatMain").click()', 6)
            print("  акт по договору: шаг", js(ws, "CH.wz"), "| сверка:",
                  js(ws, "CH.act && CH.act.contract_check && CH.act.contract_check.summary && CH.act.contract_check.summary.verdict"),
                  "| ошибка:", js(ws, "CH.err") or "нет")
            bad.append(shot_el(ws, "chat_step3_ct_390.png", 390, ".act-sum"))
            js(ws, "actReset()", 1)

            # дальше /act/photos отвечает образцом: у временного сервера нет сети к модели
            # /act/market/shots не перехватывается: загрузка снимков объявлений — настоящая
            ws.call("Fetch.enable", patterns=[{"urlPattern": "*/act/photos*", "requestStage": "Request"}])

            # ---- запрос филиала: скан 25.png, ответ модели — сохранённый ответ живого сервера (br30/photos_25.json)
            set_files(ws, "#chatFile", [BR_SCAN])
            time.sleep(1)
            js(ws, 'document.querySelector("#chatMain").click()', 0.2)
            paused = wait_event(ws, "Fetch.requestPaused")
            if paused:
                fulfill(ws, paused, br_answer())
            time.sleep(2)
            js(ws, WAIT_REFS)
            js(ws, "wzPaint(true)", 0.5)
            print("  запрос филиала: шаг", js(ws, "CH.wz"), "| продукт:", js(ws, "CH.must.product_code + ' / класс ' + CH.must.class_code"),
                  "| срок:", js(ws, "CH.opt.term_days"), "| строк:", js(ws, "CH.br && CH.br.rows_found + '/' + CH.br.rows_total"))
            js(ws, 'CH.must.region = "tashkent_city"; wzSave(); wzPaint(true); window.scrollTo(0, 0)', 0.5)
            bad.append(shot_el(ws, "chat_step2_br_390.png", 390, "#brCard"))
            bad.append(shot(ws, "chat_step2_br_full_390.png", 390, cap=7000))
            js(ws, "wzGo(1)", 1)
            bad.append(shot_el(ws, "chat_step1_br_390.png", 390, "#wzFiles"))
            js(ws, "wzGo(2)", 1)
            # правка премии сотрудником: запрос уходит как «введено сотрудником», затем «Вернуть как в документе»
            js(ws, """(() => { const el = document.querySelector('#dqbr-premium'); el.value = '122 500 000';
                       el.dispatchEvent(new Event('input', {bubbles: true})); })()""", 0.3)
            print("  после правки премии:", js(ws, "JSON.stringify({p: actBody().optional.request.premium,"
                                                   " tag: document.querySelector('#dqbr-src').innerText})"))
            js(ws, 'document.querySelector("[data-dqreset=br]").click()', 0.5)
            print("  в /act/make уйдёт запрос:", js(ws, "JSON.stringify(actBody().optional.request)"))
            js(ws, 'document.querySelector("#chatMain").click()', 7)
            print("  акт по запросу: шаг", js(ws, "CH.wz"), "| сверка:",
                  js(ws, "CH.act && CH.act.request_check && JSON.stringify(CH.act.request_check.items.map(i => i.code + ':' + i.verdict))"),
                  "| ошибка:", js(ws, "CH.err") or "нет")
            js(ws, "window.scrollTo(0, 0)", 0.3)
            bad.append(shot_el(ws, "chat_step3_rq_390.png", 390, ".act-sum"))
            bad.append(shot_el(ws, "chat_step3_rq_1440.png", 1440, ".act-sum", scale=1))
            js(ws, LANG_UZ, 4)
            print("  узбекский: язык акта", js(ws, "CH.act && CH.act.lang"))
            bad.append(shot_el(ws, "chat_step3_rq_uz_390.png", 390, ".act-sum"))
            js(ws, "wzGo(2)", 1.5)
            bad.append(shot_el(ws, "chat_step2_br_uz_390.png", 390, "#brCard"))
            js(ws, LANG_RU, 3)
            js(ws, "actReset()", 1)

            # ---- запрос филиала + договор со скана: карточка договора со скана и сверка двух документов
            set_files(ws, "#chatFile", [BR_SCAN, CT_SCAN])
            time.sleep(1)
            js(ws, 'document.querySelector("#chatMain").click()', 0.2)
            paused = wait_event(ws, "Fetch.requestPaused")
            if paused:
                fulfill(ws, paused, br_answer(with_contract=True))
            time.sleep(2)
            js(ws, WAIT_REFS)
            js(ws, 'CH.must.region = "tashkent_city"; wzSave(); wzPaint(true); window.scrollTo(0, 0)', 0.5)
            print("  запрос + договор: сверка", js(ws, "CH.xc && JSON.stringify(CH.xc.items.map(i => i.code + ':' + i.verdict))"))
            js(ws, "wzGo(1)", 1)
            bad.append(shot_el(ws, "chat_step1_brct_390.png", 390, "#wzFiles"))
            js(ws, "wzGo(2)", 1)
            bad.append(shot_el(ws, "chat_step2_ct_scan_390.png", 390, "#ctCard"))
            bad.append(shot_el(ws, "chat_step2_xc_390.png", 390, "#xcCard"))
            js(ws, 'document.querySelector("#chatMain").click()', 7)
            print("  акт по запросу и договору: шаг", js(ws, "CH.wz"), "| cross:",
                  js(ws, "CH.act && CH.act.cross_check && CH.act.cross_check.differs"), "| ошибка:", js(ws, "CH.err") or "нет")
            bad.append(shot_el(ws, "chat_step3_xc_390.png", 390, ".act-sum"))
            js(ws, "actReset()", 1)

            # три снимка в списке: превью, «Убрать», кнопка «Дальше»
            set_files(ws, "#chatFile", FILES)
            time.sleep(1.5)
            bad.append(shot(ws, "chat_step1_file_390.png", 390))
            js(ws, 'document.querySelector("#chatMain").click()', 0.2)       # «Дальше» — POST /act/photos
            paused = wait_event(ws, "Fetch.requestPaused")
            if paused:
                time.sleep(0.6)
                js(ws, "window.scrollTo(0, 0)", 0.2)
                bad.append(shot(ws, "chat_step1_reading_390.png", 390))
                ws.call("Fetch.fulfillRequest", requestId=paused["requestId"], responseCode=200,
                        responseHeaders=[{"name": "Content-Type", "value": "application/json; charset=utf-8"}],
                        body=base64.b64encode(photos_answer()).decode())
            else:
                print("  запрос /act/photos не пойман — шаг «Проверить» без распознанного")
            time.sleep(2)
            print("  после «Дальше»: шаг", js(ws, "CH.wz"), "| распознано:", js(ws, "CH.rec.length"),
                  "| не хватает ракурсов:", js(ws, "CH.missingViews.join(', ')") or "—", "| ошибка:", js(ws, "CH.err") or "нет")

            # шаг 2 «Проверить»: распознанное с источниками и расхождением, продукт, суммы, регион
            js(ws, FILL_STEP2, 3)
            js(ws, "window.scrollTo(0, 0)", 0.3)
            bad.append(shot(ws, "chat_step2_390.png", 390, cap=5000))
            # исправление значения сотрудником: источник меняется на «введено сотрудником»
            js(ws, """(() => { const el = document.querySelector('[data-rec="1"]'); el.value = 'XCMG Group';
                       el.dispatchEvent(new Event('input', {bubbles: true})); el.value = 'XCMG';
                       el.dispatchEvent(new Event('input', {bubbles: true})); })()""", 0.3)
            js(ws, 'CH.optOpen = true; wzPart("more")', 0.5)
            bad.append(shot(ws, "chat_step2_more_390.png", 390, cap=6000))
            js(ws, 'CH.prodOpen = true; wzPart("prod"); window.scrollTo(0, 0)', 1)
            bad.append(shot(ws, "chat_step2_products_390.png", 390, cap=2400))
            js(ws, 'CH.prodOpen = false; CH.prodQ = ""; wzPart("prod")', 0.5)

            # оценка по объявлениям: карточка пустая — ссылки поиска по распознанному (XCMG QY50K5D, автокран)
            js(ws, "wzPaint(true)", 2.5)
            print("  ссылки поиска:", js(ws, "JSON.stringify((MK.links ? MK.links.links : []).map(l => l.site + ' ' + l.url))"),
                  "| ошибка:", js(ws, "MK.linksErr") or "нет")
            bad.append(shot_el(ws, "chat_step2_market_390.png", 390, "#mkCard"))
            # снимок экрана со списком → «Прочитать объявления»: настоящий POST /act/market/shots
            # (во временном сервере подменена только модель — она отвечает образцом sandbox/mk30/shots.json)
            set_files(ws, "#mkFile", [LISTINGS_SHOT])
            time.sleep(1)
            js(ws, 'document.querySelector("[data-mk=read]").click()', 0.05)
            bad.append(shot_el(ws, "chat_step2_market_reading_390.png", 390, "#mkShots"))
            for _ in range(60):
                if not js(ws, "MK.busy"):
                    break
                time.sleep(0.25)
            print("  загрузка снимков:", js(ws, "MK.ss") or "нет", "| сообщение:", js(ws, "MK.info && MK.info.message") or "—",
                  "| ошибка:", js(ws, "MK.err") or "нет")
            # курса ЦБ у временного сервера нет (сети нет): сотрудник вводит курс сам — в акте «введён сотрудником»
            js(ws, """(() => { const el = document.querySelector('#mkRate'); if (!el) return;
                       el.value = '""" + USD_RATE + """'; el.dispatchEvent(new Event('input', {bubbles: true})); })()""", 0.5)
            print("  объявления:", js(ws, "MK.listings.length"), "| предпросмотр:",
                  js(ws, "(() => { const e = mkEstimate(); return [e.median, e.low, e.high, e.used + '/' + e.count, e.verdict,"
                         " mkDiff(CH.must.object_value, e.median)].join(' | '); })()"), "| ошибка:", js(ws, "MK.err") or "нет")
            bad.append(shot_el(ws, "chat_step2_market_read_390.png", 390, "#mkCard"))
            bad.append(shot_el(ws, "chat_step2_market_read_1440.png", 1440, "#mkCard", scale=1))
            # форма «Добавить объявление вручную» — открыть, посмотреть и закрыть (в акт не уходит)
            js(ws, 'document.querySelector("[data-mk=addopen]").click()', 0.5)
            bad.append(shot_el(ws, "chat_step2_market_add_390.png", 390, "#mkAdd"))
            js(ws, 'document.querySelector("[data-mk=addcancel]").click()', 0.3)

            # «Сформировать акт» → шаг 3 «Акт» (настоящий POST /act/make)
            js(ws, 'document.querySelector("#chatMain").click()', 6)
            print("  после «Сформировать акт»: шаг", js(ws, "CH.wz"), "| акт:", js(ws, "CH.act && CH.act.number") or "нет",
                  "| решение:", js(ws, "CH.act && CH.act.decision.code") or "—",
                  "| расхождений:", js(ws, "CH.act ? CH.act.discrepancies.length : -1"), "| ошибка:", js(ws, "CH.err") or "нет")
            js(ws, "window.scrollTo(0, 0)", 0.5)
            print("  без франшизы: статус", js(ws, "CH.act && CH.act.franchise.status"), "| премия", js(ws, "CH.act && CH.act.premium.amount"),
                  "| сценарии:", js(ws, "CH.act && CH.act.scenarios.available"), "| мероприятий:", js(ws, "CH.act ? CH.act.measures.length : -1"))
            bad.append(shot(ws, "chat_step3_390.png", 390, cap=9000))
            # плитка «Оценка по объявлениям» в сводке и раздел 3 со строками источника
            print("  оценка в акте:", js(ws, "(() => { const m = CH.act && CH.act.market_value; return m ? [m.verdict, m.median,"
                                             " m.low + '–' + m.high, m.used + '/' + m.count, m.diff_pct, m.refined_value,"
                                             " m.insured_check && m.insured_check.ratio_pct, m.source_label].join(' | ') : 'нет'; })()"))
            bad.append(shot_el(ws, "chat_step3_market_390.png", 390, ".act-sum"))
            js(ws, "document.querySelectorAll('details.act-s').forEach(d => { d.open = d.dataset.sn === '3'; })", 0.3)
            bad.append(shot_el(ws, "chat_step3_market_doc_390.png", 390, 'details.act-s[data-sn="3"]'))
            bad.append(shot_el(ws, "chat_step3_market_1440.png", 1440, ".act-sum", scale=1))
            js(ws, "wzPaint(true)", 0.5)

            # шаг 2: блок «Франшиза» раскрыт — своя безусловная 1 % от страховой суммы
            js(ws, "wzGo(2)", 1)
            js(ws, 'CH.optOpen = true; wzPart("more")', 0.5)
            js(ws, """(() => { document.querySelector('[data-fr="on"][data-value="yes"]').click();
                       const el = document.querySelector('#wzo-frval'); el.value = '1';
                       el.dispatchEvent(new Event('input', {bubbles: true}));
                       document.querySelector('#wzFr').scrollIntoView({block: 'start'}); })()""", 0.8)
            bad.append(shot(ws, "chat_step2_fr_390.png", 390, cap=7000))
            js(ws, 'document.querySelector("#chatMain").click()', 6)
            print("  франшиза 1 %: статус", js(ws, "CH.act && CH.act.franchise.status"),
                  "| было → стало", js(ws, "CH.act && [CH.act.franchise.premium_before, CH.act.franchise.premium_after].join(' → ')"),
                  "| франшиза, сум", js(ws, "CH.act && CH.act.franchise.size_amount"), "| ошибка:", js(ws, "CH.err") or "нет")
            js(ws, "window.scrollTo(0, 0)", 0.5)
            bad.append(shot(ws, "chat_step3_fr_390.png", 390, cap=9000))
            bad.append(shot(ws, "chat_step3_1440.png", 1440, cap=7000, scale=1))
            # как шаг «Акт» выглядит в Telegram: кнопки «Прислать … в чат» и ответ «нажмите Старт у бота»
            # (headless Edge не Telegram — подставляем разметку тех же функций страницы)
            js(ws, """(() => { const b = document.querySelector('.act-btns');
                       b.innerHTML = ['docx', 'pdf'].map(actSendBtnHtml).join('') + b.querySelector('[data-go="copy"]').outerHTML;
                       actMsg(errHtml(T('tg.act.send_start', 'Бот пока не может вам написать. Откройте бота, нажмите «Старт» и вернитесь — затем отправьте акт ещё раз.'))
                         + '<div class="act-send-more"><button type="button" class="btn btn-secondary">'
                         + esc(T('tg.act.open_bot', 'Открыть бота')) + '</button><button type="button" class="btn btn-secondary">'
                         + esc(T('tg.act.send_again', 'Отправить ещё раз')) + '</button></div>'); window.scrollTo(0, 0); })()""", 0.5)
            bad.append(shot(ws, "chat_step3_tg_390.png", 390, cap=2600))
            js(ws, "wzPaint(true)", 0.5)

            # узбекский: подписи из словаря, акт — GET /act/{id}?lang=uz; введённое не теряется
            js(ws, "document.querySelector('[data-lang-pick=\"uz\"]').click()", 4)
            print("  узбекский: язык акта", js(ws, "CH.act && CH.act.lang"), "| регион в полях:", js(ws, "CH.must.region"))
            js(ws, "window.scrollTo(0, 0)", 0.3)
            bad.append(shot(ws, "chat_step3_uz_390.png", 390, cap=9000))
            bad.append(shot_el(ws, "chat_step3_market_uz_390.png", 390, ".act-sum"))
            js(ws, "document.querySelectorAll('details.act-s').forEach(d => { d.open = d.dataset.sn === '3'; })", 0.3)
            bad.append(shot_el(ws, "chat_step3_market_doc_uz_390.png", 390, 'details.act-s[data-sn="3"]'))
            js(ws, "wzGo(2)", 2.5)
            bad.append(shot(ws, "chat_step2_uz_390.png", 390, cap=5000))
            bad.append(shot_el(ws, "chat_step2_market_uz_390.png", 390, "#mkCard"))
            js(ws, "document.querySelector('[data-lang-pick=\"ru\"]').click()", 3)

            # предложенная франшиза: мелкие убытки за три года — система советует, сотрудник применяет одной кнопкой
            js(ws, 'CH.fr = FR_OFF(); Object.assign(CH.opt, {losses_count: "2", losses_small: "2"}); wzSave(); wzPaint(true)', 0.5)
            js(ws, 'document.querySelector("#chatMain").click()', 6)
            print("  предложение: статус", js(ws, "CH.act && CH.act.franchise.status"), "| размер",
                  js(ws, "CH.act && CH.act.franchise.size_pct"), "| премия с ней", js(ws, "CH.act && CH.act.franchise.premium_after"))
            js(ws, "window.scrollTo(0, 0)", 0.5)
            bad.append(shot(ws, "chat_step3_prop_390.png", 390, cap=3200))
            js(ws, "(() => { const b = document.querySelector('[data-go=\"frapply\"]'); if (b) b.click(); })()", 7)
            print("  после «Применить»: шаг", js(ws, "CH.wz"), "| статус", js(ws, "CH.act && CH.act.franchise.status"),
                  "| в блоке шага 2:", js(ws, "JSON.stringify(CH.fr)"))
            js(ws, 'CH.fr = FR_OFF(); Object.assign(CH.opt, {losses_count: "0"}); delete CH.opt.losses_small; wzSave()', 0.2)

            # «Новый акт» → «Без фото»: сразу шаг 2 без распознанного
            js(ws, "wzGo(3)", 1)
            js(ws, "document.querySelector('[data-go=\"restart\"]').click()", 1)
            js(ws, 'document.querySelector("#chatMain").click()', 1)
            js(ws, "window.scrollTo(0, 0)", 0.3)
            bad.append(shot(ws, "chat_nophoto_390.png", 390, cap=3000))

            # аналитика риска на шаге «Акт»: автокран и оборудование класса 8, 390 / 1440 px, узбекский
            shoot_analytics(ws, bad)
            # страховой скоринг объекта и отчёт кредитного бюро (01.10.2026)
            shoot_scoring(ws, bad)

            # остальные разделы в той же палитре
            js(ws, 'openTab("calc")', 3)
            bad.append(shot(ws, "tab_calc_390.png", 390, cap=2400))
            js(ws, 'openTab("legal")', 3)
            bad.append(shot(ws, "tab_legal_390.png", 390, cap=2400))
            js(ws, 'openTab("osgor")', 3)
            bad.append(shot(ws, "tab_osgor_1440.png", 1440, cap=2400, scale=1))

            # тёмная тема остаётся рабочей
            ws.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/tg?theme=dark")
            time.sleep(6)
            bad.append(shot(ws, "chat_dark_390.png", 390))
            errs = console_errors(ws)

            # комплексный продукт 0312 по частям, поля класса 13, ракурсы из шаблона, админка шаблонов
            ws.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/tg?theme=light")
            time.sleep(6)
            errs += shoot_parts(ws, bad, token=inst.token)
            print("итог: горизонтальной прокрутки нет" if not any(x > 0 for x in bad)
                  else "ВНИМАНИЕ: где-то есть горизонтальная прокрутка: " + str(bad))
            print("ошибок в консоли нет" if not errs else "ОШИБКИ В КОНСОЛИ:\n  " + "\n  ".join(errs))
        finally:
            edge.terminate()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve_fake_model(int(sys.argv[2]), Path(sys.argv[3]))
    else:
        main(only_analytics="--analytics" in sys.argv[1:], only_parts="--parts" in sys.argv[1:],
             only_scoring="--scoring" in sys.argv[1:])
