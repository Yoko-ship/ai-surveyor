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

Запуск из корня проекта:
    sandbox\\.venv\\Scripts\\python.exe tools\\shoot_chat.py
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
  const t = document.querySelector(".wrap").innerText;
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
        elif m == "Log.entryAdded" and (p.get("entry") or {}).get("level") == "error":
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


def main():
    OUT.mkdir(exist_ok=True)
    with ShootInstance(PORT, "dev", keep=False):
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
            print("итог: горизонтальной прокрутки нет" if not any(x > 0 for x in bad)
                  else "ВНИМАНИЕ: где-то есть горизонтальная прокрутка: " + str(bad))
            print("ошибок в консоли нет" if not errs else "ОШИБКИ В КОНСОЛИ:\n  " + "\n  ".join(errs))
        finally:
            edge.terminate()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve_fake_model(int(sys.argv[2]), Path(sys.argv[3]))
    else:
        main()
