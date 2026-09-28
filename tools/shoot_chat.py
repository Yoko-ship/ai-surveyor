"""
Скриншоты вкладки «ИИ-сюрвейер» на копии базы (задача 223; мастер из трёх шагов — 28.09.2026).

Поднимает временный экземпляр сервера (tools/perf_check.Instance — копия базы, рабочая не трогается),
запускает headless Edge с отладочным портом и водит страницу по шагам через CDP (стандартная
библиотека, без пакетов): шаг «Фото» → файл договора (sandbox/flow150_contract.docx) → шаг «Проверить»
(в том числе открытый список продуктов) → «Считаем» → шаг «Анализ» на 390 и 1440 px → узбекский →
тёмная тема и соседние разделы. Складывает PNG в sandbox/, печатает горизонтальную прокрутку,
служебные слова на экране (undefined, null, calibrated…) и ошибки консоли страницы.

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
SAMPLE = ROOT / "sandbox" / "flow150_contract.docx"      # учебный договор, без данных клиентов

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


FILL_STEP2 = """(async () => {
  for (let i = 0; i < 40 && !CH.refs; i++) await new Promise(r => setTimeout(r, 250));
  if (!CH.draft.must.product_code && !CH.draft.must.class_code) wzPickProduct("0807");
  const m = CH.draft.must;
  if (!(Number(m.sum_insured) > 0)) m.sum_insured = 1000000000;
  if (!(Number(m.object_value) > 0)) m.object_value = 1200000000;
  for (let i = 0; i < 40 && !(CH.meta && (CH.meta.regions || []).length); i++) await new Promise(r => setTimeout(r, 250));
  const r = (CH.meta && CH.meta.regions) || [];
  if (!m.region && r.length) m.region = typeof r[1] === "string" ? r[1] : (r[1] || r[0]).name;
  ["product_code", "class_code", "sum_insured", "object_value", "region"].forEach(k => { CH.dirty.must[k] = 1; });
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
            ws.call("Emulation.setDeviceMetricsOverride", width=390, height=900, deviceScaleFactor=2, mobile=True)
            ws.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/tg")
            time.sleep(7)
            bad = []
            # шаг 1 «Фото»: пусто — внизу «Без документов» (обложки нет, сразу раздел)
            bad.append(shot(ws, "chat_step1_390.png", 390))

            # файл договора в списке: строка файла, «Убрать», кнопка «Дальше»
            if SAMPLE.exists():
                set_files(ws, "#chatFile", [SAMPLE])
                time.sleep(1.5)
                bad.append(shot(ws, "chat_step1_file_390.png", 390))
                js(ws, 'document.querySelector("#chatMain").click()', 12)      # «Дальше» — загрузка
            else:
                print("  нет sandbox/flow150_contract.docx — шаг «Фото» без файла")
                js(ws, 'document.querySelector("#chatMain").click()', 3)       # «Без документов»
            print("  после «Дальше»: шаг", js(ws, "CH.wz"), "| файлов отправлено:", js(ws, "CH.sent.length"),
                  "| из документа:", js(ws, "Object.keys(CH.fromDoc).join(', ')") or "—")

            # шаг 2 «Проверить»: продукт, суммы, регион — то, чего нет в договоре
            js(ws, FILL_STEP2, 4)
            js(ws, "window.scrollTo(0, 0)", 0.3)
            bad.append(shot(ws, "chat_step2_390.png", 390))
            # список продуктов «как в тарифной политике»
            js(ws, 'CH.prodOpen = true; wzPart("prod"); window.scrollTo(0, 0)', 1)
            bad.append(shot(ws, "chat_step2_products_390.png", 390, cap=2400))
            js(ws, 'CH.prodOpen = false; CH.prodQ = ""; wzPart("prod")', 0.5)

            # «Считаем» → шаг 3 «Анализ»
            js(ws, 'document.querySelector("#chatMain").click()', 14)
            print("  после «Считаем»: шаг", js(ws, "CH.wz"), "| ошибка:", js(ws, "CH.err") or "нет",
                  "| подробности:", "есть" if js(ws, "!!CH.full") else js(ws, "CH.fullErr") or "грузятся")
            js(ws, "window.scrollTo(0, 0)", 0.5)
            bad.append(shot(ws, "chat_step3_390.png", 390))
            # все блоки подробностей открыты — внутри не должно быть служебных слов и переполнения
            js(ws, 'Object.assign(CH.open, {fr: true, opts: true, value: true, risks: true, scn: true, level: true,'
                   ' region: true, docs: true, why: true, say: true}); wzPaint(true)', 3)
            bad.append(shot(ws, "chat_step3_open_390.png", 390, cap=16000, scale=1))
            js(ws, "Object.keys(CH.open).forEach(k => { CH.open[k] = false; }); CH.open.fr = true; wzPaint(true)", 1.5)
            bad.append(shot(ws, "chat_step3_1440.png", 1440, cap=6000, scale=1))

            # узбекский: подписи из словаря, свободный текст переводит сервер; введённое не теряется
            js(ws, "Object.keys(CH.open).forEach(k => { CH.open[k] = false; }); wzPaint(true)", 0.5)
            js(ws, "document.querySelector('[data-lang-pick=\"uz\"]').click()", 8)
            bad.append(shot(ws, "chat_step3_uz_390.png", 390))
            js(ws, "wzGo(2)", 2)
            bad.append(shot(ws, "chat_step2_uz_390.png", 390))
            print("  после смены языка: сумма", js(ws, "CH.draft.must.sum_insured"), "| регион", js(ws, "CH.draft.must.region"))
            js(ws, "document.querySelector('[data-lang-pick=\"ru\"]').click()", 5)

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
