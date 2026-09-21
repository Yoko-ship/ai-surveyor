"""
Замер скорости сервера на КОПИИ рабочей базы: «до» и «после» оптимизации.

Рабочий сервер (127.0.0.1:8000) и рабочая база data/surveyor.db не трогаются:
  * база копируется штатным резервным копированием SQLite (снимок целостный, источник открыт только на чтение);
  * временный экземпляр app.main:app поднимается uvicorn'ом в подпроцессе на отдельном порту (по умолчанию 8765)
    с STORAGE_DIR = временный каталог — база, загрузки и фото живут там;
  * в экземпляре выключены все фоновые потоки (НАПП, stat.uz, законы, доклад, автоимпорт, бот Telegram),
    а любые сетевые соединения, кроме 127.0.0.1, запрещены и считаются (в журнале экземпляра: «perf: сеть закрыта»);
  * модули, которые открывают data/surveyor.db мимо app/db.py (tools/market_stats.py), перенаправляются на копию.

Запуск (из корня проекта, питон из sandbox\\.venv — там fastapi и uvicorn):

    set PYTHONIOENCODING=utf-8
    set PY=sandbox\\.venv\\Scripts\\python.exe
    rem один раз: замороженная копия базы (sandbox\\ не публикуется) — «до» и «после» меряются на одних данных
    %PY% tools\\perf_check.py --freeze sandbox\\perf\\baseline.db
    %PY% tools\\perf_check.py --source-db sandbox\\perf\\baseline.db --label до --json data\\perf\\baseline\\perf_до.json
    %PY% tools\\perf_check.py --source-db sandbox\\perf\\baseline.db --label до --load --json data\\perf\\baseline\\load_до.json
    %PY% tools\\perf_check.py --source-db sandbox\\perf\\baseline.db --snapshot data\\perf\\baseline\\calc
    %PY% tools\\perf_check.py --source-db sandbox\\perf\\baseline.db --compare data\\perf\\baseline\\calc
    %PY% tools\\perf_check.py --source-db sandbox\\perf\\baseline.db --diag

Ключи:
  --label TEXT      метка замера (до / после), попадает в JSON
  --json PATH       куда сохранить результат
  --runs N          прогонов на точку (20), --warmup N — прогревочных (2)
  --load            нагрузка: волны по 20 параллельных POST /calculate + фоновая запись в базу
  --snapshot DIR    сохранить тела ответов /calculate по 10 продуктам побайтно
  --compare DIR     сравнить с сохранённым снимком (побайтно; при расхождении — без нестабильных полей)
  --diag            замеры внутри процесса: load_reference, connect, PRAGMA копии
  --auth session|dev  session (по умолчанию): вход по сессии, как на Railway — Bearer-токен тестового
                    администратора, заведённого только в копии; dev: SURVEYOR_DEV=1 (guard пропускает 127.0.0.1,
                    но точки с Depends(current_user) всё равно требуют сессию — токен передаётся и тут)
  --port N          порт временного экземпляра (8765)
  --keep            не удалять временный каталог (для разбора журнала экземпляра)
  --freeze PATH     сохранить целостную копию data/surveyor.db в файл и выйти
  --source-db PATH  откуда брать базу для временного экземпляра (по умолчанию data/surveyor.db)
"""
import argparse
import gzip
import json
import math
import os
import platform
import secrets
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from datetime import datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROD_DB = ROOT / "data" / "surveyor.db"
VENV_PY = ROOT / "sandbox" / ".venv" / "Scripts" / "python.exe"

PERF_LOGIN = "perf-admin"

# ------------------------------------------------------------------------------------------------
#  Тела запросов
# ------------------------------------------------------------------------------------------------

WAREHOUSE = {"product_code": "0807", "class_code": "8", "object_type": "Склад",
             "value_amount": 4.2e9, "sum_insured": 4.2e9,
             "factors": {"construction": "mixed", "activity": "warehouse", "protection": "alarm",
                         "seismic": "z8", "wear": "mid", "loss_history": "clean", "franchise": "f0"}}
KASKO = {"product_code": "0311", "class_code": "3", "object_type": "Легковой",
         "value_amount": 146.1e6, "sum_insured": 146.1e6, "payer_type": "юр",
         "factors": {"veh_age": "a3", "veh_type": "car", "antitheft": "alarm",
                     "drivers": "limited", "loss_history": "clean", "franchise": "f1"}}
CREDIT = {"product_code": "1404", "class_code": "14", "object_type": "Кредит",
          "value_amount": 100e6, "sum_insured": 40e6,
          "factors": {"credit_collateral": "partial", "credit_term": "t36",
                      "credit_industry": "trade", "credit_history": "clean"},
          "credit": {"loan_amount": 100e6, "collateral_value": 60e6,
                     "policyholder_is_bank": True, "payer_is_bank": True}}
# ОСГОР через общий движок: продукт 1323, класс 13 (ответственность работодателя)
OSGOR_CALC = {"product_code": "1323", "class_code": "13", "object_type": "Ответственность",
              "value_amount": 4.8e9, "sum_insured": 4.8e9,
              "factors": {"liab_activity": "trade", "liab_claims": "clean", "franchise": "f1"}}

# снимок расчётов: 4 основных + 6 других классов (кредит, 8/9, груз, ответственность, НС, нормативный тариф)
SNAPSHOT = [
    ("0807_sklad", WAREHOUSE),
    ("0311_kasko", KASKO),
    ("1404_credit", CREDIT),
    ("1323_osgor", OSGOR_CALC),
    ("0808_cls9", {"product_code": "0808", "class_code": "9", "object_type": "Производство",
                   "value_amount": 2e9, "sum_insured": 1.5e9,
                   "factors": {"loss_history": "clean", "franchise": "f1"}}),
    ("0824_cls16_8_9", {"product_code": "0824", "class_code": "8", "object_type": "Магазин",
                        "value_amount": 900e6, "sum_insured": 900e6,
                        "factors": {"construction": "wood", "activity": "trade", "protection": "none",
                                    "seismic": "z9", "wear": "old", "loss_history": "clean", "franchise": "f1"}}),
    ("0701_cargo", {"product_code": "0701", "class_code": "7", "object_type": "Груз",
                    "value_amount": 800e6, "sum_insured": 800e6,
                    "factors": {"cargo_mode": "auto", "cargo_pack": "container", "cargo_route": "cis",
                                "cargo_transship": "t1", "cargo_nature": "fragile",
                                "loss_history": "clean", "franchise": "f1"}}),
    ("1329_liability", {"product_code": "1329", "class_code": "13", "object_type": "Общегражданская ответственность",
                        "value_amount": 500e6, "sum_insured": 500e6,
                        "factors": {"liab_activity": "trade", "liab_turnover": "t10",
                                    "liab_claims": "clean", "liab_limit": "l1000", "franchise": "f1"}}),
    ("0101_ns", {"product_code": "0101", "class_code": "1", "object_type": "Человек",
                 "value_amount": 50e6, "sum_insured": 50e6,
                 "factors": {"loss_history": "clean", "franchise": "f0"}}),
    ("1002_statutory", {"product_code": "1002", "class_code": "10", "object_type": "Легковой",
                        "value_amount": 100e6, "sum_insured": 100e6,
                        "applied_rate_pct": 0.01, "manual_reason": "ставка филиала"}),
]

OSGOR_ASSESS = {"name": "ООО «Перф-тест»", "okved": "41100", "headcount": 50, "payroll_12m": 900e6,
                "attestation_done": True, "safety_service": True, "training_done": True, "ppe_provided": True}

VALUATION = {"object_type": "авто", "params": {"brand": "Chevrolet", "model": "Nexia", "year": 2022},
             "as_of": "2026-09-20", "declared_value": 90e6, "norm_code": "vehicles_other",
             "initial_value": 200e6, "commissioned_at": "2022-09-20", "mileage_km": 160000,
             "use_market": False, "created_by": "perf"}

# запрос для выгрузок: страхователь — юрлицо, данных граждан нет
REQUEST = dict(WAREHOUSE, factors={"construction": "wood", "activity": "warehouse", "protection": "none",
                                   "seismic": "z9", "wear": "old", "loss_history": "clean", "franchise": "f0"},
               sum_insured=4.0e9, branch="perf", policyholder="ООО «Перф-тест»", region="Ташкент",
               address="ул. Складская, 1", external_no="PERF-001",
               docs_received=["Заявление-анкета на страхование", "Документ о праве на объект"])

HTML = "text/html,application/xhtml+xml"
JSON_ = "application/json"


def points(rid):
    """(имя, метод, путь, тело, Accept). rid — запрос, созданный в копии базы."""
    p = [
        ("GET /health", "GET", "/health", None, JSON_),
        ("GET /tg", "GET", "/tg", None, HTML),
        ("GET /ui", "GET", "/ui", None, HTML),
        ("GET /admin/hub", "GET", "/admin/hub", None, HTML),
        ("GET /stats", "GET", "/stats", None, HTML),
        ("POST /calculate 0807 склад", "POST", "/calculate", WAREHOUSE, JSON_),
        ("POST /calculate 0311 КАСКО", "POST", "/calculate", KASKO, JSON_),
        ("POST /calculate 1404 кредит", "POST", "/calculate", CREDIT, JSON_),
        ("POST /calculate 1323 ОСГОР", "POST", "/calculate", OSGOR_CALC, JSON_),
        ("POST /osgor/assess", "POST", "/osgor/assess", OSGOR_ASSESS, JSON_),
        ("POST /valuations/estimate (без рынка)", "POST", "/valuations/estimate", VALUATION, JSON_),
        ("GET /market/series", "GET", "/market/series?row=cls8_9", None, JSON_),
        ("GET /stat/indicators", "GET", "/stat/indicators", None, JSON_),
        ("GET /tg/me", "GET", "/tg/me", None, JSON_),
        ("GET /approvals/pending", "GET", "/approvals/pending", None, JSON_),
    ]
    if rid:
        p += [
            ("GET /requests/{id}/proposal.pdf", "GET", f"/requests/{rid}/proposal.pdf", None, "application/pdf"),
            ("GET /requests/{id}/analysis.pdf", "GET", f"/requests/{rid}/analysis.pdf", None, "application/pdf"),
            ("GET /requests/{id}/analysis.xlsx", "GET", f"/requests/{rid}/analysis.xlsx", None, "*/*"),
        ]
    return p


# Поля ответа /calculate, которые зависят не от кода, а от ДАННЫХ базы.
# Между двумя прогонами подряд на одной базе ответ совпадает побайтно (проверяется при каждом --snapshot:
# всё, что разойдётся, попадёт в manifest.json → unstable_between_runs и тоже будет исключаться — явно).
# Эти поля меняются, только если изменились данные; сравнение идёт сначала побайтно, а при расхождении —
# повторно без них, и отчёт называет, какие именно отличались. Пути — после нормализации:
# checks и recommendations превращаются в словари по коду правила / виду.
DATA_DEPENDENT = {
    "market.*": "рыночный ориентир НАПП: последний срез market_stats (обновляет агент-статистик)",
    "checks.risk_limit_20.detail": "предел 20% на риск и дата отчётности — последняя строка company_financials",
    "probability.stat.*": "поправка по накопленным решениям: decision_outcomes растёт с каждым решением",
}
# Поля, зависящие от сегодняшней даты (load_reference берёт версии тарифов, действующие на today).
# Их НЕ исключаем: смена минимальной ставки — изменение результата, его надо увидеть. Поэтому снимок
# и сверку «до/после» делаем на одной замороженной копии базы (--source-db) и отмечаем день снимка.
DATE_DEPENDENT = ["rates.min_pct", "rates.min_source", "rates.applied_pct", "premium", "verdict"]


# ------------------------------------------------------------------------------------------------
#  Копия базы и перенаправление прямых обращений к рабочей базе
# ------------------------------------------------------------------------------------------------

def copy_db(dest: Path, source: Path = PROD_DB):
    """Целостный снимок базы: источник открыт только на чтение, пишем только в копию."""
    src = sqlite3.connect(f"file:{Path(source).resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def redirect_prod_db(copy: Path):
    """Любой sqlite3.connect к data/surveyor.db уходит в копию (tools/market_stats.py открывает её напрямую)."""
    real = sqlite3.connect
    prod = os.path.normcase(str(PROD_DB.resolve()))
    hits = {"n": 0}

    def connect(database, *a, **kw):
        try:
            if isinstance(database, (str, Path)) and not str(database).startswith("file:") \
                    and os.path.normcase(str(Path(database).resolve())) == prod:
                hits["n"] += 1
                database = str(copy)
        except OSError:
            pass
        return real(database, *a, **kw)

    sqlite3.connect = connect
    return hits


def add_perf_admin(copy: Path) -> str:
    """Тестовый администратор и сессия — только в копии. Пароль не нужен: вход по токену."""
    con = sqlite3.connect(copy)
    try:
        con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE login=?)", (PERF_LOGIN,))
        con.execute("DELETE FROM users WHERE login=?", (PERF_LOGIN,))
        now = datetime.now().isoformat(timespec="seconds")
        cur = con.execute(
            "INSERT INTO users (login, full_name, role, branch, password_hash, salt, status, created_at,"
            " approved_by, approved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (PERF_LOGIN, "Perf Admin", "админ", "perf", secrets.token_hex(32), secrets.token_hex(16),
             "активен", now, "perf", now))
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions (token, user_id, created_at, expires_at, ip, user_agent) VALUES (?,?,?,?,?,?)",
                    (token, cur.lastrowid, now, (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds"),
                     "127.0.0.1", "perf_check"))
        con.commit()
        return token
    finally:
        con.close()


# ------------------------------------------------------------------------------------------------
#  Временный экземпляр сервера (подпроцесс, режим --serve)
# ------------------------------------------------------------------------------------------------

def serve(port: int, copy: Path):
    """Выполняется в подпроцессе: без фоновых потоков, без сети наружу, база — копия."""
    sys.path.insert(0, str(ROOT))
    redirect_prod_db(copy)

    real_gai = socket.getaddrinfo
    real_connect = socket.socket.connect
    local = {"127.0.0.1", "::1", "localhost"}

    def blocked(what):
        print(f"perf: сеть закрыта: {what}", file=sys.stderr, flush=True)
        return OSError(f"perf_check: сеть закрыта ({what})")

    def gai(host, *a, **kw):
        if host not in local and host is not None:
            raise blocked(host)
        return real_gai(host, *a, **kw)

    def sconnect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in local:
            raise blocked(host)
        return real_connect(self, address)

    socket.getaddrinfo = gai
    socket.socket.connect = sconnect

    from app import main as m
    from app import team, statagency, lawwatch, tgbot
    m._scheduler = lambda: None               # НАПП
    m._inbox_watcher = lambda: None           # автоимпорт выгрузок
    team.start_scheduler = lambda: None       # ежедневный доклад
    statagency.start_scheduler = lambda: None  # stat.uz
    lawwatch.start_scheduler = lambda: None   # законы
    tgbot.start_polling = lambda: False       # бот

    import uvicorn
    uvicorn.run(m.app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def server_python() -> str:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        return sys.executable
    except ImportError:
        return str(VENV_PY)


class Instance:
    def __init__(self, port: int, auth: str, keep: bool, source: Path = PROD_DB):
        self.port, self.auth, self.keep, self.source = port, auth, keep, Path(source)
        self.dir = Path(tempfile.mkdtemp(prefix="surveyor-perf-"))
        self.db = self.dir / "surveyor.db"
        self.log = self.dir / "server.log"
        self.proc = None
        self.token = None

    def __enter__(self):
        if _port_busy(self.port):
            raise SystemExit(f"порт {self.port} занят — выберите другой: --port")
        t = time.perf_counter()
        copy_db(self.db, self.source)
        self.copy_sec = time.perf_counter() - t
        self.token = add_perf_admin(self.db)
        env = {k: v for k, v in os.environ.items()
               if k not in ("SURVEYOR_DEV", "TG_POLLING", "DEMO_SEED", "STORAGE_DIR")}
        env.update({"STORAGE_DIR": str(self.dir), "PYTHONIOENCODING": "utf-8", "TG_POLLING": "0"})
        if self.auth == "dev":
            env["SURVEYOR_DEV"] = "1"
        self._logf = open(self.log, "w", encoding="utf-8")
        t = time.perf_counter()
        self.proc = subprocess.Popen([server_python(), str(Path(__file__).resolve()), "--serve",
                                      "--port", str(self.port), "--db", str(self.db)],
                                     cwd=str(ROOT), env=env, stdout=self._logf, stderr=subprocess.STDOUT)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit("временный экземпляр не поднялся:\n" + self.log.read_text(encoding="utf-8")[-3000:])
            try:
                c = HTTPConnection("127.0.0.1", self.port, timeout=2)
                c.request("GET", "/health")
                if c.getresponse().status == 200:
                    c.close()
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("временный экземпляр не ответил на /health за 90 с")
        self.start_sec = time.perf_counter() - t
        return self

    def log_text(self) -> str:
        self._logf.flush()
        return self.log.read_text(encoding="utf-8", errors="replace")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._logf.close()
        if self.keep:
            print(f"временный каталог оставлен: {self.dir}")
        else:
            # Windows отпускает файлы завершённого процесса не сразу — несколько попыток
            for _ in range(20):
                shutil.rmtree(self.dir, ignore_errors=True)
                if not self.dir.exists():
                    break
                time.sleep(0.5)


def _port_busy(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------
#  Клиент
# ------------------------------------------------------------------------------------------------

class Client:
    """HTTP/1.1 с keep-alive, как браузер: Accept-Encoding gzip, Bearer-токен."""

    def __init__(self, port: int, token: str):
        self.port, self.token = port, token
        self.con = None

    def _conn(self):
        if self.con is None:
            self.con = HTTPConnection("127.0.0.1", self.port, timeout=180)
        return self.con

    def call(self, method, path, body=None, accept=JSON_):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Accept": accept, "Accept-Encoding": "gzip, deflate",
                   "Authorization": f"Bearer {self.token}", "User-Agent": "perf_check"}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        for attempt in (1, 2):
            try:
                t = time.perf_counter()
                c = self._conn()
                c.request(method, path, body=data, headers=headers)
                r = c.getresponse()
                raw = r.read()
                ms = (time.perf_counter() - t) * 1000
                if (r.getheader("Connection") or "").lower() == "close":
                    self.close()
                enc = r.getheader("Content-Encoding") or ""
                return {"status": r.status, "ms": ms, "wire": len(raw), "enc": enc,
                        "ctype": r.getheader("Content-Type") or "", "body": _decode(raw, enc)}
            except (OSError, ConnectionError) as e:   # сервер закрыл keep-alive — один повтор на новом соединении
                self.close()
                if attempt == 2:
                    return {"status": 0, "ms": 0.0, "wire": 0, "enc": "", "ctype": "", "body": str(e).encode()}

    def close(self):
        if self.con is not None:
            self.con.close()
            self.con = None


def _decode(raw: bytes, enc: str) -> bytes:
    enc = enc.lower()
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc == "deflate":
        return zlib.decompress(raw)
    return raw


def pct(values, q):
    """Процентиль по ближайшему рангу: для 20 прогонов p95 — 19-е значение по возрастанию."""
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


# ------------------------------------------------------------------------------------------------
#  Режимы
# ------------------------------------------------------------------------------------------------

def create_request(cl: Client) -> int:
    r = cl.call("POST", "/requests", REQUEST)
    if r["status"] != 200:
        print("запрос для выгрузок не создан:", r["status"], r["body"][:300])
        return 0
    return json.loads(r["body"])["request_id"]


def run_bench(inst: Instance, runs: int, warmup: int) -> list:
    cl = Client(inst.port, inst.token)
    rid = create_request(cl)
    out = []
    for name, method, path, body, accept in points(rid):
        for _ in range(warmup):
            cl.call(method, path, body, accept)
        samples, last = [], None
        codes = set()
        for _ in range(runs):
            last = cl.call(method, path, body, accept)
            samples.append(last["ms"])
            codes.add(last["status"])
        out.append({"point": name, "method": method, "path": path.replace(str(rid), "{id}") if rid else path,
                    "status": sorted(codes), "median_ms": round(statistics.median(samples), 1),
                    "p95_ms": round(pct(samples, 0.95), 1), "min_ms": round(min(samples), 1),
                    "max_ms": round(max(samples), 1), "bytes_wire": last["wire"], "bytes_body": len(last["body"]),
                    "content_encoding": last["enc"] or "—", "content_type": last["ctype"].split(";")[0]})
        print(f"  {name:<40} {out[-1]['median_ms']:>8} мс", flush=True)
    cl.close()
    return out


def print_table(rows):
    head = f"{'точка':<40} {'код':>5} {'медиана':>9} {'p95':>9} {'макс':>9} {'байт (сеть)':>12} {'байт (тело)':>12}  сжатие"
    print(head)
    print("-" * len(head))
    for r in rows:
        code = ",".join(str(c) for c in r["status"])
        print(f"{r['point']:<40} {code:>5} {r['median_ms']:>9} {r['p95_ms']:>9} {r['max_ms']:>9}"
              f" {r['bytes_wire']:>12} {r['bytes_body']:>12}  {r['content_encoding']}")


def writer_loop(copy: Path, stop: threading.Event, stats: dict):
    """
    Фоновая запись во время нагрузки — то же, что делает сервер сам:
      * пересборка рыночной статистики tools/market_stats.build() (как агент-статистик после скачивания;
        держит блокировку записи всё время разбора CSV);
      * между пересборками — короткие записи в журнал audit, как db.audit через db.tx (таймаут 5 с по умолчанию).
    Сеть не нужна: CSV уже лежат в data/parsed (только чтение).
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import market_stats                          # sqlite3.connect уже перенаправлен на копию
    market_stats.DB = copy
    while not stop.is_set():
        t = time.perf_counter()
        try:
            market_stats.build()
            stats["builds"] += 1
            stats["build_ms"].append(round((time.perf_counter() - t) * 1000))
        except sqlite3.OperationalError as e:
            stats["errors"].append(f"build: {e}")
        for _ in range(10):
            if stop.is_set():
                break
            try:
                con = sqlite3.connect(copy)
                con.execute("PRAGMA foreign_keys = ON")
                con.execute("INSERT INTO audit (ts, who, action, entity, detail) VALUES (?,?,?,?,?)",
                            (datetime.now().isoformat(timespec="seconds"), "perf", "фоновая запись", "perf", None))
                con.commit()
                con.close()
                stats["audit_writes"] += 1
            except sqlite3.OperationalError as e:
                stats["errors"].append(f"audit: {e}")
            time.sleep(0.05)


def run_load(inst: Instance, waves: int, parallel: int) -> dict:
    products = [WAREHOUSE, KASKO, CREDIT, OSGOR_CALC]
    redirect_prod_db(inst.db)                   # market_stats при импорте читает data/surveyor.db — уводим в копию
    log_before = len(inst.log_text())
    stats = {"builds": 0, "build_ms": [], "audit_writes": 0, "errors": []}
    stop = threading.Event()
    w = threading.Thread(target=writer_loop, args=(inst.db, stop, stats), daemon=True)
    w.start()
    time.sleep(0.3)                             # писатель успевает взять блокировку
    results, lock = [], threading.Lock()
    clients = [Client(inst.port, inst.token) for _ in range(parallel)]
    t0 = time.perf_counter()
    for wave in range(waves):
        barrier = threading.Barrier(parallel)

        def one(i):
            barrier.wait()
            r = clients[i].call("POST", "/calculate", products[(i + wave) % len(products)])
            with lock:
                results.append(r)

        ts = [threading.Thread(target=one, args=(i,)) for i in range(parallel)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    total_sec = time.perf_counter() - t0
    stop.set()
    w.join(120)
    for c in clients:
        c.close()
    time.sleep(0.5)
    log = inst.log_text()[log_before:]
    ok = [r for r in results if r["status"] == 200]
    bad = [r for r in results if r["status"] != 200]
    locked_body = sum(1 for r in bad if b"database is locked" in r["body"])
    ms = [r["ms"] for r in results if r["status"]]
    return {
        "waves": waves, "parallel": parallel, "requests": len(results), "ok": len(ok),
        "errors": len(bad), "status_codes": sorted({r["status"] for r in results}),
        "database_is_locked_in_server_log": log.count("database is locked"),
        "database_is_locked_in_responses": locked_body,
        "tracebacks_in_server_log": log.count("Traceback"),
        "median_ms": round(statistics.median(ms), 1) if ms else None,
        "p95_ms": round(pct(ms, 0.95), 1) if ms else None,
        "max_ms": round(max(ms), 1) if ms else None,
        "wall_sec": round(total_sec, 2), "rps": round(len(results) / total_sec, 1) if total_sec else None,
        "writer": {"market_stats_builds": stats["builds"], "build_ms": stats["build_ms"],
                   "audit_writes": stats["audit_writes"], "errors": stats["errors"][:20],
                   "errors_total": len(stats["errors"]),
                   "locked_errors": sum(1 for e in stats["errors"] if "locked" in e)},
    }


def _flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def _normal(body: bytes):
    """checks и recommendations — словари по коду правила / виду: пути не зависят от порядка в списке."""
    d = json.loads(body)
    if isinstance(d, dict):
        if isinstance(d.get("checks"), list):
            d["checks"] = {c.get("rule", str(i)): c for i, c in enumerate(d["checks"])}
        if isinstance(d.get("recommendations"), list):
            d["recommendations"] = {f"{r.get('kind')}#{i}": r for i, r in enumerate(d["recommendations"])}
    return d


# таблицы, из которых собирается расчёт (db.load_reference, to_input, analysis.probability)
FINGERPRINT_TABLES = ("products", "product_classes", "base_rates", "coefficients", "perils", "load_components",
                      "min_rates", "tariff_versions", "checklists", "company_financials", "preventive_measures",
                      "market_stats", "rules", "decision_outcomes", "valuations")


def data_fingerprint(path: Path) -> dict:
    """Отпечаток данных, влияющих на расчёт: при сверке видно, менялись ли данные, а не код."""
    import hashlib
    con = sqlite3.connect(path)
    out = {}
    try:
        for t in FINGERPRINT_TABLES:
            try:
                h = hashlib.sha256()
                for r in con.execute(f"SELECT * FROM {t} ORDER BY 1"):
                    h.update(repr(tuple(r)).encode("utf-8"))
                out[t] = h.hexdigest()[:16]
            except sqlite3.Error:
                out[t] = None
    finally:
        con.close()
    return out


def _unstable(path: str, patterns) -> bool:
    for p in patterns:
        if p.endswith(".*") and (path == p[:-2] or path.startswith(p[:-2] + ".") or path.startswith(p[:-2] + "[")):
            return True
        if path == p:
            return True
    return False


def run_snapshot(inst: Instance, folder: Path):
    """Два прогона подряд: всё, что разошлось между ними, — нестабильно и записывается в манифест явно."""
    folder.mkdir(parents=True, exist_ok=True)
    cl = Client(inst.port, inst.token)
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "products": [],
                "unstable_between_runs": {}, "data_dependent": DATA_DEPENDENT,
                "date_dependent": DATE_DEPENDENT, "today": datetime.now().date().isoformat(),
                "source_db": str(inst.source), "data_fingerprint": data_fingerprint(inst.db)}
    for name, body in SNAPSHOT:
        a = cl.call("POST", "/calculate", body)
        b = cl.call("POST", "/calculate", body)
        (folder / f"{name}.json").write_bytes(a["body"])
        (folder / f"{name}.request.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        diff = []
        if a["body"] != b["body"]:
            fa, fb = _flatten(_normal(a["body"])), _flatten(_normal(b["body"]))
            diff = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
        manifest["products"].append({"name": name, "status": a["status"], "bytes": len(a["body"]),
                                     "same_bytes_twice": a["body"] == b["body"]})
        if diff:
            manifest["unstable_between_runs"][name] = diff
        print(f"  {name:<18} код {a['status']}  {len(a['body']):>7} байт  повтор {'совпал' if not diff else 'РАЗОШЁЛСЯ: ' + ', '.join(diff[:5])}")
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    cl.close()
    return manifest


def run_compare(inst: Instance, folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    patterns = set(DATA_DEPENDENT)
    for paths in manifest.get("unstable_between_runs", {}).values():
        patterns.update(paths)
    cl = Client(inst.port, inst.token)
    report = {"same_bytes": 0, "same_without_unstable": 0, "different": [], "unstable_diffs": {},
              "today_then": manifest.get("today"), "today_now": datetime.now().date().isoformat()}
    now_fp = data_fingerprint(inst.db)
    report["data_changed_tables"] = sorted(t for t, h in (manifest.get("data_fingerprint") or {}).items()
                                           if now_fp.get(t) != h)
    if report["data_changed_tables"]:
        print("  внимание: данные изменились с момента снимка:", ", ".join(report["data_changed_tables"]),
              "— расхождения могут быть от данных, а не от кода (сверяйте на той же копии: --source-db)")
    if report["today_then"] != report["today_now"]:
        print("  внимание: снимок сделан в другой день — действующие версии тарифов могли смениться")
    for name, body in SNAPSHOT:
        old = (folder / f"{name}.json").read_bytes()
        new = cl.call("POST", "/calculate", body)["body"]
        if old == new:
            report["same_bytes"] += 1
            print(f"  {name:<18} побайтно совпадает")
            continue
        fo, fn = _flatten(_normal(old)), _flatten(_normal(new))
        diffs = sorted(k for k in set(fo) | set(fn) if fo.get(k) != fn.get(k))
        real = [k for k in diffs if not _unstable(k, patterns)]
        if real:
            report["different"].append({"name": name, "paths": real[:50]})
            print(f"  {name:<18} РАСХОЖДЕНИЕ: {', '.join(real[:8])}")
        else:
            report["same_without_unstable"] += 1
            report["unstable_diffs"][name] = diffs
            print(f"  {name:<18} совпадает без нестабильных полей (отличались: {', '.join(diffs[:6])})")
    cl.close()
    return report


def run_diag(copy: Path) -> dict:
    """В этом же процессе, на копии: сколько стоят load_reference и открытие соединения; PRAGMA."""
    sys.path.insert(0, str(ROOT))
    os.environ["STORAGE_DIR"] = str(copy.parent)
    redirect_prod_db(copy)
    from app import db
    db.DB_PATH = copy
    out = {}
    with db.tx() as con:
        out["pragma"] = {p: con.execute(f"PRAGMA {p}").fetchone()[0]
                         for p in ("journal_mode", "synchronous", "busy_timeout", "foreign_keys",
                                   "cache_size", "page_size", "page_count", "mmap_size", "temp_store")}
    for label, fn in (("db.connect()+close", lambda: db.connect().close()),
                      ("load_reference", None)):
        ts = []
        for i in range(22):
            if fn is None:
                with db.tx() as con:
                    t = time.perf_counter()
                    db.load_reference(con)
                    ts.append((time.perf_counter() - t) * 1000)
            else:
                t = time.perf_counter()
                fn()
                ts.append((time.perf_counter() - t) * 1000)
        ts = ts[2:]
        out[label] = {"median_ms": round(statistics.median(ts), 2), "p95_ms": round(pct(ts, 0.95), 2)}
    with db.tx() as con:
        t = time.perf_counter()
        db.market_rates(con)
        out["market_rates_ms"] = round((time.perf_counter() - t) * 1000, 2)
    return out


def machine() -> dict:
    info = {"platform": platform.platform(), "processor": platform.processor(), "cpu_count": os.cpu_count(),
            "python_client": platform.python_version(), "server_python": server_python()}
    try:                                         # объём памяти — только на Windows, без сторонних пакетов
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            info["ram_gb"] = round(m.ullTotalPhys / 2 ** 30, 1)
            info["ram_load_pct"] = m.dwMemoryLoad
    except Exception:
        pass
    return info


def db_facts(copy: Path) -> dict:
    con = sqlite3.connect(copy)
    try:
        facts = {"size_bytes": copy.stat().st_size}
        for t in ("requests", "calculations", "audit", "market_stats", "stat_series", "valuations", "sessions", "users"):
            try:
                facts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                facts[t] = None
        return facts
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Замер скорости сервера на копии базы")
    ap.add_argument("--label", default="")
    ap.add_argument("--json")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--waves", type=int, default=5)
    ap.add_argument("--parallel", type=int, default=20)
    ap.add_argument("--snapshot")
    ap.add_argument("--compare")
    ap.add_argument("--diag", action="store_true")
    ap.add_argument("--auth", choices=("session", "dev"), default="session")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--source-db", help="откуда копировать базу (по умолчанию data/surveyor.db)")
    ap.add_argument("--freeze", help="сохранить целостную копию рабочей базы в файл и выйти")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--db", help=argparse.SUPPRESS)
    a = ap.parse_args()

    if a.serve:
        serve(a.port, Path(a.db))
        return

    if a.freeze:
        dest = Path(a.freeze)
        if dest.resolve() == PROD_DB.resolve():
            raise SystemExit("--freeze не может писать в рабочую базу")
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_db(dest)
        print("замороженная копия:", dest, dest.stat().st_size, "байт")
        return
    source = Path(a.source_db) if a.source_db else PROD_DB
    if a.port == 8000:
        raise SystemExit("порт 8000 — рабочий сервер; замер идёт только на временном экземпляре")
    result = {"label": a.label, "source_db": str(source), "started": datetime.now().isoformat(timespec="seconds"),
              "machine": machine(), "auth": a.auth, "port": a.port, "runs": a.runs, "warmup": a.warmup}

    if a.diag:
        d = Path(tempfile.mkdtemp(prefix="surveyor-perf-diag-"))
        try:
            copy_db(d / "surveyor.db", source)
            result["diag"] = run_diag(d / "surveyor.db")
            print(json.dumps(result["diag"], ensure_ascii=False, indent=1))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    else:
        with Instance(a.port, a.auth, a.keep, source) as inst:
            result["db"] = db_facts(inst.db)
            result["copy_sec"] = round(inst.copy_sec, 2)
            result["startup_sec"] = round(inst.start_sec, 2)
            print(f"копия базы: {inst.db} ({result['db']['size_bytes']} байт), старт экземпляра {result['startup_sec']} с")
            if a.snapshot:
                result["snapshot"] = run_snapshot(inst, Path(a.snapshot))
            elif a.compare:
                result["compare"] = run_compare(inst, Path(a.compare))
            elif a.load:
                result["load"] = run_load(inst, a.waves, a.parallel)
                print(json.dumps(result["load"], ensure_ascii=False, indent=1))
            else:
                result["points"] = run_bench(inst, a.runs, a.warmup)
                print()
                print_table(result["points"])
            log = inst.log_text()
            result["net_blocked"] = log.count("perf: сеть закрыта")
            result["server_tracebacks"] = log.count("Traceback")
            if result["net_blocked"]:
                print(f"внимание: экземпляр пытался выйти в сеть {result['net_blocked']} раз(а) — см. журнал (--keep)")
    result["finished"] = datetime.now().isoformat(timespec="seconds")
    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print("JSON:", p)


if __name__ == "__main__":
    main()
