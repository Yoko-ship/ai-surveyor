"""
Скриншоты вкладки «ИИ-сюрвейер», лёгкая версия (ТЗ 2.0 от 29.09.2026): «Фото → Проверить → Акт».

Поднимает временный экземпляр сервера (tools/perf_check.Instance — копия базы, рабочая не трогается),
запускает headless Edge с отладочным портом и водит страницу по шагам через CDP (стандартная
библиотека, без пакетов). Сети к языковой модели у временного сервера нет, поэтому ответ POST /act/photos
подменяется настоящим ответом сервера из sandbox/act_demo_photos.json (+ одно значение с таблички,
чтобы было видно расхождение источников). Дальше всё настоящее: POST /act/make и GET /act/{id}?lang=uz.
Путь: шаг «Фото» пустой → три снимка в списке → «Читаю фото…» → шаг «Проверить» с распознанным →
«Сформировать акт» → шаг «Акт» на 390 и 1440 px → узбекский → «Новый акт» → «Без фото» → тёмная тема
и соседние разделы. Складывает PNG в sandbox/, печатает горизонтальную прокрутку, служебные слова
на экране (undefined, null, calibrated…) и ошибки консоли страницы.

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
PHOTOS_SAMPLE = ROOT / "sandbox" / "act_demo_photos.json"        # настоящий ответ POST /act/photos
FILES = [ROOT / "sandbox" / "gen" / "test_1.jpg", ROOT / "sandbox" / "gen" / "test_2.jpg",
         ROOT / "sandbox" / "docx_p0.png"]                       # картинки без данных людей

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
    with Instance(PORT, "dev", keep=False):
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
            # /act/photos отвечает образцом: у временного сервера нет сети к модели
            ws.call("Fetch.enable", patterns=[{"urlPattern": "*/act/photos*", "requestStage": "Request"}])
            ws.call("Emulation.setDeviceMetricsOverride", width=390, height=900, deviceScaleFactor=2, mobile=True)
            ws.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/tg")
            time.sleep(7)
            bad = []
            # шаг 1 «Фото»: пусто — внизу «Без фото», подсказка ракурсов по всем видам объектов
            bad.append(shot(ws, "chat_step1_390.png", 390))

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

            # «Сформировать акт» → шаг 3 «Акт» (настоящий POST /act/make)
            js(ws, 'document.querySelector("#chatMain").click()', 6)
            print("  после «Сформировать акт»: шаг", js(ws, "CH.wz"), "| акт:", js(ws, "CH.act && CH.act.number") or "нет",
                  "| решение:", js(ws, "CH.act && CH.act.decision.code") or "—",
                  "| расхождений:", js(ws, "CH.act ? CH.act.discrepancies.length : -1"), "| ошибка:", js(ws, "CH.err") or "нет")
            js(ws, "window.scrollTo(0, 0)", 0.5)
            bad.append(shot(ws, "chat_step3_390.png", 390, cap=9000))
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
            js(ws, "wzGo(2)", 1.5)
            bad.append(shot(ws, "chat_step2_uz_390.png", 390, cap=5000))
            js(ws, "document.querySelector('[data-lang-pick=\"ru\"]').click()", 3)

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
    main()
