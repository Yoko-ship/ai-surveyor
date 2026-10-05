"""Изолированный офлайн-прогон собственных точек входа tests/test_*.py.

python tools/test_all.py [test_engine.py test_architecture.py]
База собирается из справочников в отдельной копии; рабочая база и .env не копируются.
"""
from pathlib import Path
from datetime import datetime
from contextlib import closing
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
CHILD = '''import runpy, socket, sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / "tests")]
connect = socket.socket.connect
def offline(sock, address):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
        return connect(sock, address)
    raise OSError("External network disabled in offline tests")
socket.socket.connect = offline
path = sys.argv[1]
sys.argv = [path]
runpy.run_path(path, run_name="__main__")
'''


def run(test_names=None):
    if not shutil.which("node") and (not test_names or any("tg_ui" in name for name in test_names)):
        raise RuntimeError("Полная проверка интерфейса требует Node.js в PATH")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = ROOT / "sandbox" / "test-runs" / stamp
    checkout = output / "checkout"
    checkout.mkdir(parents=True)
    # Код включает ещё не закоммиченные изменения; данные — только файлы репозитория.
    for name in ("app", "tools", "tests", "frontend", "db"):
        shutil.copytree(ROOT / name, checkout / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    tracked = subprocess.check_output(["git", "ls-files", "-z", "docs", "library", "data"],
                                      cwd=ROOT).decode("utf-8").split("\0")
    for name in filter(None, tracked):
        source = ROOT / name
        if source.is_file() and source.suffix not in {".db", ".sqlite", ".sqlite3"}:
            destination = checkout / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    (checkout / "sandbox").mkdir(exist_ok=True)
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
            "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(PYTHONIOENCODING="utf-8", SURVEYOR_NO_BACKGROUND="1", LLM_PROVIDER="none",
               LEX_LIVE="0", TG_POLLING="0", DEMO_SEED="0", PD_MODE="test")

    def execute(path, timeout=180):
        return subprocess.run([sys.executable, "-c", CHILD, str(path)], cwd=checkout,
                              env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)

    for step in ("tools/db_build.py", "tools/market_stats.py", "tests/prepare_fixtures.py"):
        result = execute(step)
        (output / (Path(step).stem + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Не удалось подготовить тесты: {step}; журнал {output}")
    seed = output / "seed.db"
    shutil.copy2(checkout / "data" / "surveyor.db", seed)
    scripts = sorted((checkout / "tests").glob("test_*.py"))
    if test_names:
        chosen = set(test_names)
        scripts = [p for p in scripts if p.name in chosen or p.stem in chosen]
        unknown = chosen - {n for p in scripts for n in (p.name, p.stem)}
        if unknown:
            raise ValueError(f"Неизвестные тесты: {sorted(unknown)}")
    results = []
    for test in scripts:
        # Некоторые старые инструменты используют data/, поэтому сбрасываются оба пути.
        storage = output / test.stem
        storage.mkdir()
        for path in (storage / "surveyor.db", checkout / "data" / "surveyor.db"):
            with closing(sqlite3.connect(seed)) as src, closing(sqlite3.connect(path)) as dst:
                src.backup(dst)
        env["STORAGE_DIR"] = str(storage)
        started = time.monotonic()
        try:
            result = execute(test)
            log = result.stdout + "\n" + result.stderr
            status = result.returncode
        except subprocess.TimeoutExpired as exc:
            log = (exc.stdout or b"").decode("utf-8", "replace") + (exc.stderr or b"").decode("utf-8", "replace")
            status = "timeout"
        record = {"test": test.name, "exit": status, "seconds": round(time.monotonic() - started, 1)}
        results.append(record)
        (output / (test.stem + ".log")).write_text(log, encoding="utf-8")
        (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"{'PASS' if status == 0 else 'FAIL'} {test.name} ({record['seconds']}s)", flush=True)
    passed = sum(r["exit"] == 0 for r in results)
    print(f"{passed}/{len(results)} passed; logs: {output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="*")
    raise SystemExit(run(parser.parse_args().tests))
