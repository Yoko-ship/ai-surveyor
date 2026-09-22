"""
Скриншоты вкладки «ИИ-сюрвейер» на копии базы (задача 223).

Поднимает временный экземпляр сервера (tools/perf_check.Instance — копия базы, рабочая не трогается),
запускает headless Edge с отладочным портом и водит страницу по шагам через CDP (стандартная
библиотека, без пакетов): старт → поля → итог → узбекский. Складывает PNG в sandbox/.

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


# ------------------------------------------------------------------ WebSocket (минимальный клиент)

class WS:
    def __init__(self, url: str):
        host_port, self.path = url.split("//", 1)[1].split("/", 1)
        host, port = host_port.split(":")
        self.s = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET /" + self.path + " HTTP/1.1\r\nHost: " + host_port + "\r\nUpgrade: websocket\r\n"
                        "Connection: Upgrade\r\nSec-WebSocket-Key: " + key +
                        "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        self.id = 0

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


def shot(ws, name, width, height=900):
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
            deviceScaleFactor=2, mobile=width < 700)
    time.sleep(0.6)
    metrics = ws.call("Page.getLayoutMetrics")
    full = int(min(4000, metrics["cssContentSize"]["height"]))
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=full,
            deviceScaleFactor=2, mobile=width < 700)
    time.sleep(0.4)
    data = ws.call("Page.captureScreenshot", format="png")["data"]
    path = OUT / name
    path.write_bytes(base64.b64decode(data))
    over = js(ws, "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    print(f"  {name}: {path.stat().st_size // 1024} КБ, горизонтальная прокрутка: {over} px")
    return over


def main():
    OUT.mkdir(exist_ok=True)
    with Instance(PORT, "dev", keep=False) as inst:
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
            ws.call("Page.enable")
            ws.call("Runtime.enable")
            ws.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/tg?theme=light")
            time.sleep(6)
            bad = []
            bad.append(shot(ws, "chat_390_start.png", 390))

            # шаг «четыре обязательных поля»
            js(ws, 'chatPost("/chat/answer", {key: "fields", scope: "must", fields: {}})', 3)
            bad.append(shot(ws, "chat_390_fields.png", 390))

            # заполняем и считаем
            js(ws, """(async () => {
              const r = (CH.meta && CH.meta.regions) || [];
              const reg = r.length ? (typeof r[0] === "string" ? r[0] : (r[0].name || r[0].code)) : "";
              CH.draft.must = Object.assign({}, CH.draft.must,
                {class_code: "8", sum_insured: 1000000000, object_value: 1200000000, region: reg});
              await chatFields("must");
              await chatAnalyze("quick");
            })()""", 12)
            bad.append(shot(ws, "chat_390_result.png", 390))

            # узбекский: подписи из словаря, свободный текст переводит сервер
            js(ws, 'document.querySelector(\'[data-lang-pick="uz"]\').click()', 8)
            bad.append(shot(ws, "chat_390_uz.png", 390))

            js(ws, 'document.querySelector(\'[data-lang-pick="ru"]\').click()', 6)
            bad.append(shot(ws, "chat_1440_result.png", 1440))
            print("итог: горизонтальной прокрутки нет" if not any(x > 0 for x in bad)
                  else "ВНИМАНИЕ: где-то есть горизонтальная прокрутка: " + str(bad))
        finally:
            edge.terminate()


if __name__ == "__main__":
    main()
