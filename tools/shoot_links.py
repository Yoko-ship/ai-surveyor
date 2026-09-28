"""
Скриншоты «Способов входа» и запроса доступа в админку (задача 236).

Поднимает временный экземпляр сервера на копии базы (tools/perf_check.Instance — рабочая база
не трогается), водит headless Edge через CDP (клиент WS берём из tools/shoot_chat.py) и снимает:
  sandbox/links_390.png    — профиль вошедшего: Telegram привязан, Google можно привязать;
  sandbox/adminreq_390.png — гость нажал «Запросить доступ в админку», виден ответ.

Запуск из корня проекта:
    sandbox\\.venv\\Scripts\\python.exe tools\\shoot_links.py
"""
import base64
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from perf_check import Instance, PERF_LOGIN     # noqa: E402
from shoot_chat import EDGE, WS, js             # noqa: E402

PORT, DBG = 8126, 9226
OUT = ROOT / "sandbox"

# Показательные привязки для копии базы: Telegram есть, Google ещё нет — ровно тот случай,
# ради которого карточка и сделана. Это тестовые значения, в рабочую базу они не попадают.
TG_ID, TG_NAME = "7230524397", "@insonmax"


def seed_links(dbfile: Path):
    con = sqlite3.connect(dbfile)
    try:
        uid = con.execute("SELECT id FROM users WHERE login=?", (PERF_LOGIN,)).fetchone()[0]
        now = datetime.now().isoformat(timespec="seconds")
        con.execute("DELETE FROM login_links WHERE user_id=?", (uid,))
        con.execute("INSERT INTO login_links (user_id, provider, external_id, display, linked_at, last_login_at)"
                    " VALUES (?,?,?,?,?,?)", (uid, "telegram", TG_ID, TG_NAME, now, now))
        con.execute("UPDATE users SET telegram_id=?, full_name=? WHERE id=?", (TG_ID, "Максим Мирзаумаров", uid))
        con.commit()
    finally:
        con.close()


def shot(ws, name, width=390, height=844):
    ws.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
            deviceScaleFactor=2, mobile=True)
    time.sleep(0.6)
    data = ws.call("Page.captureScreenshot", format="png")["data"]
    path = OUT / name
    path.write_bytes(base64.b64decode(data))
    over = js(ws, "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    print(f"  {name}: {path.stat().st_size // 1024} КБ, горизонтальная прокрутка: {over} px")
    return over


def open_page(ws, port, token=None):
    page = f"http://127.0.0.1:{port}/tg?theme=light&cover=off"    # обложка на снимке не нужна
    ws.call("Page.navigate", url=page)
    time.sleep(4)
    js(ws, ("try { sessionStorage.setItem('surveyor_token', %s); } catch (e) {}"
            % json.dumps(token or "")) if token else
       "try { sessionStorage.removeItem('surveyor_token'); } catch (e) {}")
    ws.call("Page.navigate", url=page)
    time.sleep(6)
    js(ws, "sideOpen(true);", 1.2)


def main():
    OUT.mkdir(exist_ok=True)
    with Instance(PORT, "dev", keep=False) as inst:
        seed_links(inst.db)
        profile = tempfile.mkdtemp(prefix="edge-links-")
        edge = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={DBG}",
                                 f"--user-data-dir={profile}", "--no-first-run", "--disable-gpu",
                                 "--window-size=390,844", "about:blank"],
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

            # 1. вошедший: карточка «Способы входа» с живыми данными GET /auth/links
            open_page(ws, PORT, inst.token)
            print("  способы входа:", js(ws, "JSON.stringify(LINKS && LINKS.can_link)"))
            bad = [shot(ws, "links_390.png")]

            # 2. гость нажал «Запросить доступ в админку»: показываем ответ сервера
            #    (состояние после POST /auth/admin-request — статус «ожидает»)
            open_page(ws, PORT, None)
            js(ws, "ADMREQ = {status: 'ожидает'}; ASK_NOTE = null; paintAskAdmin();", 1.0)
            bad.append(shot(ws, "adminreq_390.png"))
            print("итог: горизонтальной прокрутки нет" if not any(x > 0 for x in bad)
                  else "ВНИМАНИЕ: горизонтальная прокрутка: " + str(bad))
        finally:
            edge.terminate()


if __name__ == "__main__":
    main()
