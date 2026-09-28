# -*- coding: utf-8 -*-
"""
Фоновые картинки мини-аппа: генерация, обрезка водяного знака, две ширины.

Зачем. Разделы мини-аппа (app/tg.html) показывают под содержимым фотореалистичные фоны в тёмной гамме INSON.
Картинки не покупаем и не скачиваем со стоков — их рисует бесплатный сервис image.pollinations.ai
(без ключа, отвечает готовым JPEG). Внизу справа сервис ставит подпись «pollinations.ai» — её срезаем.

Что делает скрипт:
  1. по каждой сцене из SCENES просит картинку 1280x720 (сервис может отдать меньше — берём как есть);
  2. срезает нижнюю полосу с подписью (40 px в пересчёте на высоту 720);
  3. сохраняет две версии: app/static/bg/<сцена>.jpg (большая) и <сцена>-640.jpg (для телефона),
     обе JPEG качества 80 — пересчёт делает pymupdf, других пакетов не нужно;
  4. при ошибке повторяет до 3 раз, между запросами пауза 2 с;
  5. в конце печатает честный отчёт: что получилось, какого размера, что не получилось.

Запуск из корня проекта:
    set PYTHONIOENCODING=utf-8
    sandbox\.venv\Scripts\python.exe tools\gen_backgrounds.py            (только недостающие)
    sandbox\.venv\Scripts\python.exe tools\gen_backgrounds.py --force    (перерисовать все)
    sandbox\.venv\Scripts\python.exe tools\gen_backgrounds.py chat calc  (только эти сцены)
"""
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "bg"

# Общая часть запроса: тёмная гамма бренда, без людей, без текста и логотипов.
STYLE = ("photorealistic cinematic photograph, very dark moody scene, deep navy blue #1D2C8F shadows, "
         "subtle green #22A85A accent lights, wide angle, shallow depth of field, no people, no text, "
         "no letters, no logos, no watermark, high detail, film grain")

# сцена -> (что на картинке, seed). Seed фиксируем, чтобы повтор запуска давал ту же картинку.
# Сцены hero и wave были только у обложки — её убрали 28.09.2026, картинки удалены.
SCENES = {
    "chat":      ("modern large logistics warehouse at dusk, tall racks, loading docks, cold light", 23),
    "calc":      ("glass office tower interior at night, reflections in the windows, empty desks", 31),
    "osgor":     ("industrial factory workshop, row of safety helmets on a rack, soft warm light", 47),
    "specialist": ("old library wall of legal code volumes, warm lamp light on dark wood", 59),
    "documents": ("desk with rolled building blueprints and a tablet, dim lamp, top view", 67),
    "admin":     ("server room aisle, rows of racks, blue indicator lights, dark", 73),
    "particles": ("abstract dark field of tiny glowing particles, bokeh depth, navy and green", 97),
    "lines":     ("abstract long exposure light trails on dark background, navy and green streaks", 103),
}

BASE = "https://image.pollinations.ai/prompt/"
TRIES = 3
PAUSE = 2
QUALITY = 80
WATERMARK_PX = 40        # высота полосы с подписью при картинке высотой 720
SMALL_W = 640


def ask(prompt: str, seed: int) -> bytes:
    """Просит картинку у сервиса. Возвращает тело JPEG или поднимает последнюю ошибку."""
    url = (BASE + urllib.parse.quote(prompt + ". " + STYLE)
           + f"?width=1280&height=720&nologo=true&seed={seed}&model=flux")
    last = None
    for n in range(1, TRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=240) as r:
                data = r.read()
            if not data.startswith(b"\xff\xd8\xff"):
                raise ValueError(f"сервис ответил не картинкой ({len(data)} байт)")
            return data
        except Exception as e:      # сеть или очередь сервиса — пробуем ещё раз
            last = e
            print(f"    попытка {n} из {TRIES} не удалась: {e}")
            time.sleep(PAUSE * n)
    raise last


def save(data: bytes, name: str) -> list:
    """Срезает подпись сервиса и кладёт две ширины. Возвращает список (файл, байт)."""
    doc = pymupdf.open(stream=data, filetype="jpg")
    page = doc[0]
    w, h = page.rect.width, page.rect.height
    cut = h * WATERMARK_PX / 720.0
    clip = pymupdf.Rect(0, 0, w, h - cut)
    made = []
    for suffix, width in (("", w), ("-640", min(SMALL_W, w))):
        k = width / w
        pix = page.get_pixmap(matrix=pymupdf.Matrix(k, k), clip=clip)
        path = OUT / f"{name}{suffix}.jpg"
        pix.save(path, jpg_quality=QUALITY)
        made.append((path, path.stat().st_size))
    doc.close()
    return made


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv[1:]
    names = args or list(SCENES)
    unknown = [n for n in names if n not in SCENES]
    if unknown:
        print("Нет такой сцены:", ", ".join(unknown), "— есть:", ", ".join(SCENES))
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    done, skipped, failed, total = [], [], [], 0
    for name in names:
        prompt, seed = SCENES[name]
        big = OUT / f"{name}.jpg"
        if big.exists() and not force:
            size = big.stat().st_size + (OUT / f"{name}-640.jpg").stat().st_size
            skipped.append((name, size))
            total += size
            continue
        print(f"  {name}: прошу картинку…")
        try:
            data = ask(prompt, seed)
        except Exception as e:
            failed.append((name, str(e)))
            continue
        made = save(data, name)
        size = sum(s for _, s in made)
        total += size
        done.append((name, size))
        print(f"    готово: {' + '.join(f'{p.name} {s // 1024} КБ' for p, s in made)}")
        time.sleep(PAUSE)

    print("\nОтчёт")
    print(f"  нарисовано заново: {len(done)}" + ("" if not done else
          " — " + ", ".join(f"{n} ({s // 1024} КБ)" for n, s in done)))
    if skipped:
        print(f"  уже были на месте: {len(skipped)} — " + ", ".join(n for n, _ in skipped))
    if failed:
        print(f"  не получилось: {len(failed)}")
        for n, e in failed:
            print(f"    {n}: {e}")
    print(f"  всего в app/static/bg: {total // 1024} КБ")
    small = sum(p.stat().st_size for p in OUT.glob("*-640.jpg"))
    print(f"  из них телефонных (-640): {small // 1024} КБ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
