"""
Рэнкинг страховых компаний snsratings.uz (II квартал 2026, по данным openinfo.uz и napp.uz) → таблица company_rankings.

Источник: library/03_Рынок_НАПП/Рэнкинг snsratings/*.txt — текст, извлечённый из PDF (pymupdf). Таблицы в тексте идут
колонками по строкам: «компания, значение на II кв. 2025, доля, ранг, значение на II кв. 2026, доля, ранг,
изменение %, влияние на рост рынка п.п.». Заголовок таблицы (название показателя) — в конце страницы после
«НАИМЕНОВАНИЕ», единица — первой строкой шапки («млн сум», «млрд сум», «кол-во», «%», «сум»).

Три вида страниц:
  * суммарные (стр. 9–46, единица млн/млрд сум) и количественные (стр. 49–56, «кол-во») — 8 чисел на компанию;
  * относительные (стр. 59–91) — 4 числа: значение и ранг на II кв. 2025, значение и ранг на II кв. 2026.
Особенности текста: «-» на месте значения — данных нет; «-» сразу после суммы — знак минус этой суммы
(в PDF минус стоит в отдельной ячейке: «4 681,2», «-», «-0,74%» = −4 681,2 и доля −0,74 %).

Колонки таблицы:
  value_prev/value_cur, share_prev/share_cur, rank_prev/rank_cur — как в документе;
  change_pct — «Изменение, %» документа (суммарные и количественные страницы);
  growth_impact_pp — «Влияние на рост рынка, п.п.» документа (это НЕ изменение доли: вклад компании в прирост
                     итога рынка = (значение 2026 − значение 2025) / итог 2025 × 100);
  change_pp — изменение доли, п.п. = share_cur − share_prev (считается здесь); для относительных показателей
              в процентах — value_cur − value_prev, п.п.
Строка итога — company='ВСЕГО'. Имена компаний — как ключи market_stats (company:<NAME>), страховщики жизни —
is_life=1 (по отчётам НАПП: раздел «Hayotni sug'urta qilish sohasi»).

Проверки разбора (отчёт печатается и возвращается из parse()):
  * сумма по компаниям ≈ ВСЕГО (±1 %) — для сумм и количеств, по обоим периодам;
  * ранги: одинаковый ранг допустим только у одинаковых значений (в документе так ранжируются нули и пропуски);
  * число компаний на странице (обычно 36);
  * относительные показатели сверяются с суммарными там, где формула однозначна (выплаты/премии и др.).
Показатель, который не разобрался, пропускается с записью в отчёт — ничего не додумывается.

Запуск (из корня проекта):
    sandbox/.venv/Scripts/python.exe tools/ranking_parse.py            разбор и отчёт о сходимости (база не трогается)
    sandbox/.venv/Scripts/python.exe tools/ranking_parse.py --load     + загрузка в базу сервера (app/db, ensure_schema)
    ... --db <путь>       другая база (например, копия для теста)
    ... --csv <файл>      выгрузка разобранной таблицы (по умолчанию docs/Рэнкинг snsratings — 2026-Q2.csv)
Повторная загрузка идемпотентна: строки периода заменяются по ключу (период, показатель, компания).
На сервере загрузку делает старт (app/main.py → ensure_loaded): если таблица пуста или файл изменился.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FOLDER = ROOT / "library" / "03_Рынок_НАПП" / "Рэнкинг snsratings"
SOURCE_LABEL = "Рэнкинг snsratings.uz, II кв. 2026 (по данным openinfo.uz, НАПП)"
SOURCE_URL = "https://snsratings.uz"
TOTAL = "ВСЕГО"
TOL_SUM = 0.01                       # сумма по компаниям против строки ВСЕГО: ±1 %

# --------------------------------------------------------------------------- #
#  Показатели: заголовок страницы → код
# --------------------------------------------------------------------------- #

CODES = {
    # суммарные (баланс и отчёт о финансовых результатах)
    "СОВОКУПНЫЕ АКТИВЫ": "total_assets",
    "ДОЛГОСРОЧНЫЕ АКТИВЫ": "long_term_assets",
    "ОСНОВНЫЕ СРЕДСТВА И НЕМАТЕРИАЛЬНЫЕ АКТИВЫ": "fixed_intangible_assets",
    "ДОЛГОСРОЧНЫЕ ИНВЕСТИЦИИ": "long_term_investments",
    "КРАТКОСРОЧНЫЕ ИНВЕСТИЦИИ": "short_term_investments",
    "ВСЕГО ИНВЕСТИЦИИ": "total_investments",
    "ТЕКУЩИЕ АКТИВЫ": "current_assets",
    "КРАТКОСРОЧНАЯ ДЕБИТОРСКАЯ ЗАДОЛЖЕННОСТЬ": "short_term_receivables",
    "ДЕБИТОРЫ ПО СТРАХОВЫМ ОПЕРАЦИЯМ": "insurance_receivables",
    "ДЕНЕЖНЫЕ СРЕДСТВА": "cash",
    "АКЦИОНЕРНЫЙ КАПИТАЛ": "share_capital",
    "НЕРАСПРЕДЕЛЕННАЯ ПРИБЫЛЬ (НЕПОКРЫТЫЙ УБЫТОК)": "retained_earnings",
    "СОВОКУПНЫЙ КАПИТАЛ": "total_capital",
    "СТРАХОВЫЕ РЕЗЕРВЫ, БРУТТО": "reserves_gross",
    "ДОЛЯ ПЕРЕСТРАХОВЩИКОВ В СТРАХОВЫХ РЕЗЕРВАХ": "reinsurers_share_reserves",
    "СТРАХОВЫЕ РЕЗЕРВЫ, ЧИСТЫЕ": "reserves_net",
    "ТЕКУЩИЕ ОБЯЗАТЕЛЬСТВА": "current_liabilities",
    "ТЕКУЩИЕ ОБЯЗАТЕЛЬСТВА ПО СТРАХОВЫМ ОПЕРАЦИЯМ": "current_insurance_liabilities",
    "ВСЕГО ОБЯЗАТЕЛЬСТВА (БАЛАНС)": "total_liabilities",
    "СТРАХОВЫЕ ПРЕМИИ ПО ПРЯМОМУ СТРАХОВАНИЮ И СОСТРАХОВАНИЮ": "premiums_direct",
    "СТРАХОВЫЕ ПРЕМИИ ПО ДОГОВОРАМ, ПЕРЕДАННЫМ В ПЕРЕСТРАХОВАНИЕ": "premiums_ceded",
    "СТРАХОВЫЕ ПРЕМИИ ПО ДОГОВОРАМ, ПРИНЯТЫМ В ПЕРЕСТРАХОВАНИЕ": "premiums_assumed",
    "ВСЕГО СОБРАННЫЕ ПРЕМИИ": "premiums_total",
    "ЧИСТЫЕ СТРАХОВЫЕ ПРЕМИИ": "premiums_net",
    "ЧИСТАЯ ВЫРУЧКА ОТ ОКАЗАНИЯ СТРАХОВЫХ УСЛУГ": "net_revenue",
    "СЕБЕСТОИМОСТЬ ОКАЗАННЫХ СТРАХОВЫХ УСЛУГ": "cost_of_services",
    "ВАЛОВАЯ ПРИБЫЛЬ (УБЫТОК) ОТ ОКАЗАНИЯ СТРАХОВЫХ УСЛУГ": "gross_profit",
    "РАСХОДЫ ПЕРИОДА": "period_expenses",
    "РАСХОДЫ ПО РЕАЛИЗАЦИИ": "selling_expenses",
    "АДМИНИСТРАТИВНЫЕ РАСХОДЫ": "admin_expenses",
    "ДОХОДЫ ОТ ФИНАНСОВОЙ ДЕЯТЕЛЬНОСТИ": "finance_income",
    "РАСХОДЫ ПО ФИНАНСОВОЙ ДЕЯТЕЛЬНОСТИ": "finance_expenses",
    "ЧИСТЫЕ ДОХОДЫ ОТ ФИНАНСОВОЙ ДЕЯТЕЛЬНОСТИ": "net_finance_income",
    "ПРИБЫЛЬ (УБЫТОК) ДО УПЛАТЫ НАЛОГА НА ПРИБЫЛЬ": "profit_before_tax",
    "НАЛОГ НА ПРИБЫЛЬ": "income_tax",
    "ЧИСТАЯ ПРИБЫЛЬ (УБЫТОК) ОТЧЕТНОГО ПЕРИОДА": "net_profit",
    "СТРАХОВЫЕ ВЫПЛАТЫ": "claims_paid",
    "СТРАХОВЫЕ ОБЯЗАТЕЛЬСТВА": "insurance_liabilities",
    # количественные
    "КОЛИЧЕСТВО ДЕЙСТВУЮЩИХ ДОГОВОРОВ": "contracts_active",
    "КОЛИЧЕСТВО ЗАКЛЮЧЕННЫХ ДОГОВОРОВ": "contracts_new",
    "ОБЩЕЕ КОЛИЧЕСТВО ПОСТУПИВШИХ ПРЕТЕНЗИЙ": "claims_received",
    "КОЛИЧЕСТВО УРЕГУЛИРОВАННЫХ СТРАХОВЫХ ПРЕТЕНЗИЙ": "claims_settled",
    "КОЛИЧЕСТВО ВЫПЛАЧЕННЫХ ПРЕТЕНЗИЙ": "claims_paid_count",
    "КОЛИЧЕСТВО ОТКАЗАННЫХ ПРЕТЕНЗИЙ": "claims_refused",
    "КОЛИЧЕСТВО НЕУРЕГУЛИРОВАННЫХ СТРАХОВЫХ ПРЕТЕНЗИЙ": "claims_unsettled",
    "КОЛИЧЕСТВО ТЕРРИТОРИАЛЬНЫХ ПОДРАЗДЕЛЕНИЙ": "branches",
    # относительные
    "КРАТКОСРОЧНЫЕ ИНВЕСТИЦИИ / ИНВЕСТИЦИИ": "st_investments_to_investments",
    "ДОЛГОСРОЧНЫЕ ИНВЕСТИЦИИ / ИНВЕСТИЦИИ": "lt_investments_to_investments",
    "ИНВЕСТИЦИИ / АКТИВЫ": "investments_to_assets",
    "ОСНОВНЫЕ СРЕДСТВА И НЕМАТЕРИАЛЬНЫЕ АКТИВЫ / АКТИВЫ": "fixed_assets_to_assets",
    "КРАТКОСРОЧНАЯ ДЕБИТОРСКАЯ ЗАДОЛЖЕННОСТЬ / АКТИВЫ": "st_receivables_to_assets",
    "ДЕБИТОРЫ ПО СТРАХОВЫМ ОПЕРАЦИЯМ / КРАТКОСРОЧНАЯ ДЕБ. ЗАДОЛЖНОСТЬ": "ins_receivables_to_st_receivables",
    "ДЕНЕЖНЫЕ СРЕДСТВА / АКТИВЫ": "cash_to_assets",
    "СОБСТВЕННЫЙ КАПИТАЛ / АКТИВЫ": "capital_to_assets",
    "АКЦИОНЕРНЫЙ КАПИТАЛ / СОБСТВЕННЫЙ КАПИТАЛ": "share_capital_to_capital",
    "НЕРАСПРЕДЕЛЕННАЯ ПРИБЫЛЬ / СОБСТВЕННЫЙ КАПИТАЛ": "retained_to_capital",
    "ТЕКУЩИЕ ОБЯЗАТЕЛЬСТВА / ПАССИВЫ": "current_liabilities_to_passives",
    "ТЕК. ОБЯЗ. ПО СТРАХОВЫМ ОБЯЗ. / ТЕКУЩИЕ ОБЯЗАТЕЛЬСТВА": "ins_current_liabilities_to_current",
    "ВСЕГО ОБЯЗАТЕЛЬСТВА (БАЛАНС) / ПАССИВЫ": "liabilities_to_passives",
    "СТРАХОВЫЕ РЕЗЕРВЫ, ЧИСТЫЕ / ПАССИВЫ": "reserves_net_to_passives",
    "СТРАХОВЫЕ РЕЗЕРВЫ, ВСЕГО / СОВОКУПНЫЙ КАПИТАЛ": "reserves_to_capital",
    "СТРАХОВЫЕ РЕЗЕРВЫ, ВСЕГО / СОБРАННЫЕ ПРЕМИЯ, ВСЕГО": "reserves_gross_to_premiums",
    "СТРАХОВЫЕ РЕЗЕРВЫ, ЧИСТЫЕ / СОБРАННЫЕ ПРЕМИЯ, ЧИСТЫЕ": "reserves_net_to_net_premiums",
    "ПЕРЕДАННЫЕ В ПЕРЕСТРАХОВАНИЕ / СОБРАННЫЕ ПРЕМИЯ, ВСЕГО": "ceded_to_premiums",
    "СОБСТВЕННЫЙ КАПИТАЛ / СОБРАННЫЕ ПРЕМИЯ, ЧИСТЫЕ": "capital_to_net_premiums",
    "ЧИСТАЯ ВЫРУЧКА ОТ ОКАЗАНИЯ УСЛУГ / СОБРАННЫЕ ПРЕМИЯ, ВСЕГО": "revenue_to_premiums",
    "СЕБЕСТОИМОСТЬ ОКАЗАННЫХ УСЛУГ / СОБРАННЫЕ ПРЕМИЯ, ВСЕГО": "cost_to_premiums",
    "АДМИНИСТРАТИВНЫЕ РАСХОДЫ / РАСХОДЫ ПЕРИОДА": "admin_to_period_expenses",
    "РЕНТАБЕЛЬНОСТЬ АКТИВОВ": "roa",
    "РЕНТАБЕЛЬНОСТЬ АКЦИОНЕРНОГО КАПИТАЛА": "roe_share_capital",
    "РЕНТАБЕЛЬНОСТЬ СОБСТВЕННОГО КАПИТАЛА": "roe",
    "СТРАХОВЫЕ ВЫПЛАТЫ / СТРАХОВЫЕ ПРЕМИИ": "payouts_to_premiums",
    "СТРАХОВЫЕ ОБЯЗАТЕЛЬСТВА / СТРАХОВЫЕ ПРЕМИИ": "liabilities_to_premiums",
    "СТРАХОВЫЕ ОБЯЗАТЕЛЬСТВА / КОЛ-ВО ДЕЙСТВУЮЩИХ ДОГОВОРОВ": "liabilities_per_contract",
    "ЗАКЛЮЧЕННЫЕ ДОГОВОРА / ДЕЙСТВУЮЩИЕ ДОГОВОРА": "new_to_active_contracts",
    "УРЕГУЛИРОВАННЫЕ ПРЕТЕНЗИИ / ВСЕГО СТРАХОВЫЕ ПРЕТЕНЗИИ": "settled_to_claims",
    "ВЫПЛАЧЕННЫЕ ПРЕТЕНЗИИ / ВСЕГО СТРАХОВЫЕ ПРЕТЕНЗИИ": "paid_to_claims",
    "ОТКАЗАННЫЕ ПРЕТЕНЗИИ / ВСЕГО СТРАХОВЫЕ ПРЕТЕНЗИИ": "refused_to_claims",
    "СТРАХОВЫЕ ВЫПЛАТЫ / КОЛ-ВО ВЫПЛАЧЕННЫХ ПРЕТЕНЗИЙ": "avg_payout",
}

# Названия по-русски: из содержания (стр. 5–6); то, чего в содержании нет, — здесь.
EXTRA_NAMES = {"branches": "Количество территориальных подразделений"}
# понятнее, чем в содержании («Выплачено», «Чистая прибыль … / Всего активы»): формула остаётся в скобках
NAME_OVERRIDE = {
    "claims_paid_count": "Количество выплаченных претензий",
    "claims_refused": "Количество отказанных претензий",
    "roa": "Рентабельность активов (чистая прибыль / всего активы)",
    "roe": "Рентабельность собственного капитала (чистая прибыль / совокупный капитал)",
    "roe_share_capital": "Рентабельность акционерного капитала (чистая прибыль / акционерный капитал)",
    "refused_to_claims": "Доля отказов (отказано / поступило претензий)",
    "paid_to_claims": "Доля выплаченных претензий (выплачено / поступило претензий)",
    "settled_to_claims": "Доля урегулированных претензий (урегулировано / поступило претензий)",
}
# Стр. 73: содержание — «Страховые резервы, чистые / Совокупный капитал», заголовок страницы — «…, ВСЕГО / …»;
# подпись выбирается пересчётом итога рынка (parse → check_relative).
# «Страховые выплаты / Страховые премии» (стр. 84) и «Страховые обязательства / Страховые премии» (стр. 85):
# знаменатель в документе на ~1,2 % больше «Всего собранных премий» (стр. 31) — вероятно, премии по отчёту НАПП;
# значения берутся как в документе, расхождение — в отчёте о сходимости.

SECTIONS = {"млн сум": "суммарные", "млрд сум": "суммарные", "кол-во": "количественные"}

# --------------------------------------------------------------------------- #
#  Компании: имя в рэнкинге → ключ market_stats (company:<NAME>) и признак «жизнь»
# --------------------------------------------------------------------------- #

COMPANIES = {
    "APEX INSURANCE": ("APEX INSURANCE AJ", 0),
    "O`ZBEKINVEST": ("O'ZBEKINVEST EISK AJ", 0),
    "EUROASIA INSURANCE": ("EUROASIA INSURANCE AJ QK", 0),
    "KAFOLAT SUG`URTA KOMPANIYASI": ("KAFOLAT SUG'URTA KOMPANIYASI AJ", 0),
    "MY-INSURANCE": ("MY-INSURANCE AJ", 0),
    "GROSS SUG`URTA KOMPANIYASI": ("GROSS SUG'URTA KOMPANIYASI AJ", 0),
    "ALFA INVEST SUG`URTA KOMPANIYASI": ("ALFA INVEST SUG'URTA KOMPANIYASI AJ", 0),
    "KAPITAL SUG`URTA": ("KAPITAL SUG'URTA AJ", 0),
    "SQB INSURANCE SUG`URTA KOMPANIYASI": ("SQB INSURANCE SUG'URTA KOMPANIYASI AJ", 0),
    "ASIA INSURANCE SUG`URTA KOMPANIYASI": ("ASIA INSURANCE SUG'URTA KOMPANIYASI AJ", 0),
    "MOSAIC INSURANCE GROUP": ("MOSAIC INSURANCE GROUP AJ QK", 0),
    "TEMIRYO`L SUG`URTA": ("TEMIRYO'L SUG'URTA AJ", 0),
    "O`ZAGROSUG`URTA": ("O'ZAGROSUG'URTA AJ", 0),
    "IMKON-SUG`URTA": ("IMKON SUG'URTA AJ", 0),
    "TRUST-INSURANCE": ("TRUST-INSURANCE AJ", 0),
    "XALQ SUG`URTA": ("XALQ SUG'URTA AJ", 0),
    "INSON": ("INSON AJ", 0),
    "ALSKOM SUG`URTA KOMPANIYASI": ("ALSKOM AJ", 0),
    "KAFIL-SUG`URTA": ("KAFIL SUG'URTA AJ", 0),
    "IMPEX-INSURANCE": ("IMPEX-INSURANCE AJ", 0),
    "INFINITY INSURANCE": ("INFINITY INSURANCE AJ", 0),
    "NEO INSURANCE CORP": ("NEO INSURANCE CORP AJ", 0),
    "QUANTUM INSURANCE": ("QUANTUM INSURANCE AJ", 0),
    "ISHONCH SUG`URTA KOMPANIYASI": ("ISHONCH SUG'URTA KOMPANIYASI AJ", 0),
    "HAMKOR SUG`URTA": ("HAMKOR SUG'URTA AJ", 0),
    "O`ZBEKISTON QAYTA SUG`URTA KOMPANIYASI": ("O'ZBEKISTON QAYTA SUG'URTA KOMPANIYASI MQST AJ", 0),
    "ARIA SUG`URTA TASHKILOTI": ("ARIA SUG'URTA TASHKILOTI AJ", 0),
    "SEMURG INSURANCE": ("SEMURG SUG'URTA AJ QK", 0),
    "GLOBAL INSURANCE GROUP": ("GLOBAL INSURANCE GROUP AJ", 0),
    # страхование жизни (в отчётах НАПП — раздел «Hayotni sug'urta qilish sohasi»; в market_stats их строк нет,
    # имя построено по тому же правилу, что ключи market_stats: название из отчёта НАПП + AJ)
    "APEX LIFE INSURANCE": ("APEX LIFE INSURANCE AJ", 1),
    "ALFA LIFE INSURANCE": ("ALFA LIFE INSURANCE AJ", 1),
    "KAFOLAT HAYOT INSURANCE": ("KAFOLAT HAYOT INSURANCE AJ", 1),
    "O`ZBEKINVEST HAYOT SUG`URTA KOMPANIYASI": ("O'ZBEKINVEST HAYOT SUG'URTA KOMPANIYASI AJ", 1),
    "AGROS HAYOT SUG`URTA KOMPANIYASI": ("AGROS HAYOT AJ", 1),
    "TBC SUG`URTA": ("TBC SUG'URTA AJ", 1),
    "PRESTIGE INSURANCE": ("PRESTIGE INSURANCE AJ", 1),
}
INSON = "INSON AJ"

# --------------------------------------------------------------------------- #
#  Разбор
# --------------------------------------------------------------------------- #

NUM_RE = re.compile(r"^-?\d{1,3}(?: \d{3})*(?:,\d+)?%?$|^-?\d+(?:,\d+)?%?$")
PAGE_RE = re.compile(r"=== стр (\d+) ===\n")


def source_file() -> Path | None:
    files = sorted(FOLDER.glob("*.txt")) if FOLDER.exists() else []
    return files[-1] if files else None


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def period_of(text: str) -> str:
    """«II квартал 202 6 года» на обложке → '2026-Q2'."""
    head = re.sub(r"\s+", "", text[:600])
    m = re.search(r"(I{1,3}|IV)квартал(20\d\d)", head)
    if not m:
        raise ValueError("на обложке нет «<квартал> квартал <год>»")
    q = {"I": 1, "II": 2, "III": 3, "IV": 4}[m.group(1)]
    return f"{m.group(2)}-Q{q}"


def pages(text: str) -> dict:
    parts = PAGE_RE.split(text)
    return {int(parts[i]): [l.strip() for l in parts[i + 1].split("\n") if l.strip()] for i in range(1, len(parts), 2)}


def is_num(tok: str) -> bool:
    return bool(NUM_RE.match(tok))


def num(tok: str):
    if tok is None or tok == "-":
        return None
    return float(tok.replace(" ", "").replace("%", "").replace(",", "."))


def contents(pg: dict) -> dict:
    """Содержание (стр. 5–6): номер страницы → название показателя."""
    out, acc = {}, []
    skip = {"СОДЕРЖАНИЕ", "Суммарные показатели", "Количественные показатели", "Относительные показатели"}
    for p in (5, 6):
        for l in pg.get(p, [])[1:]:
            if l in skip:
                acc = []
                continue
            if re.fullmatch(r"\d{1,3}", l):
                if acc:
                    out[int(l)] = " ".join(acc)
                acc = []
            else:
                acc.append(l)
    return out


def _title(lines: list) -> str:
    i = lines.index("НАИМЕНОВАНИЕ")
    return re.sub(r"\s+", " ", " ".join(lines[i + 1:])).strip()


class Row:
    """Разбор чисел одной строки таблицы по ожидаемой раскладке."""

    def __init__(self, toks: list):
        self.t, self.i, self.bad = toks, 0, None

    def take(self, kind: str):
        """kind: v — сумма/количество (может быть «-» или с отдельным минусом), p — процент, r — ранг."""
        if self.i >= len(self.t):
            self.bad = f"не хватает чисел (ожидалось ещё {kind})"
            return None
        tok = self.t[self.i]
        self.i += 1
        if tok == "-":
            return None
        if not is_num(tok):
            self.bad = f"не число: {tok!r}"
            return None
        if kind == "r":
            if "%" in tok or "," in tok:
                self.bad = f"ранг не целое: {tok!r}"
                return None
            return int(tok)
        if kind == "p" and "%" not in tok:
            self.bad = f"ожидался процент: {tok!r}"
            return None
        val = num(tok)
        # «4 681,2», «-», «-0,74%» — отдельный минус перед долей относится к сумме
        if kind == "v" and "%" not in tok and self.i + 1 < len(self.t) and self.t[self.i] == "-" \
                and "%" in self.t[self.i + 1]:
            self.i += 1
            val = -val
        return val

    def done(self) -> bool:
        if self.bad is None and self.i != len(self.t):
            self.bad = f"лишние значения: {self.t[self.i:]}"
        return self.bad is None


def _split_rows(lines: list) -> list:
    """[(имя, [токены])] от ВСЕГО до подвала «2025_II»."""
    i = lines.index(TOTAL)
    end = lines.index("2025_II") if "2025_II" in lines else len(lines)
    out = []
    for tok in lines[i:end]:
        if tok == TOTAL or (not is_num(tok) and tok != "-" and re.search(r"[A-Za-zА-Яа-я]", tok)):
            out.append([tok, []])
        elif out:
            out[-1][1].append(tok)
    return out


def parse_page(p: int, lines: list, names: dict, period: str, src: str, issues: list) -> list:
    title = _title(lines)
    code = CODES.get(title)
    if not code:
        issues.append(f"стр. {p}: показатель «{title}» не в словаре — пропущен")
        return []
    unit = lines[1]
    relative = "доля" not in lines[1:8]
    section = "относительные" if relative else SECTIONS.get(unit, "суммарные")
    name = NAME_OVERRIDE.get(code) or names.get(p) or EXTRA_NAMES.get(code) or title.capitalize()
    out = []
    for comp, toks in _split_rows(lines):
        r = Row(toks)
        if comp == TOTAL:
            if relative:
                vp, vc = r.take("v"), r.take("v")
                chg = None
            else:
                vp, vc, chg = r.take("v"), r.take("v"), r.take("p")
            rec = dict(company=TOTAL, company_raw=TOTAL, is_life=0, value_prev=vp, share_prev=None, rank_prev=None,
                       value_cur=vc, share_cur=None, rank_cur=None, change_pct=chg, growth_impact_pp=None)
        else:
            key = COMPANIES.get(comp)
            if not key:
                issues.append(f"стр. {p} ({code}): компания «{comp}» не сопоставлена — строка пропущена")
                continue
            if relative:
                vp, rp, vc, rc = r.take("v"), r.take("r"), r.take("v"), r.take("r")
                sp = sc = chg = imp = None
            else:
                vp, sp, rp = r.take("v"), r.take("p"), r.take("r")
                vc, sc, rc = r.take("v"), r.take("p"), r.take("r")
                chg, imp = r.take("p"), r.take("p")
            rec = dict(company=key[0], company_raw=comp, is_life=key[1], value_prev=vp, share_prev=sp, rank_prev=rp,
                       value_cur=vc, share_cur=sc, rank_cur=rc, change_pct=chg, growth_impact_pp=imp)
        if not r.done():
            issues.append(f"стр. {p} ({code}), {comp}: {r.bad} — строка пропущена")
            continue
        if relative:
            rec["change_pp"] = (round(rec["value_cur"] - rec["value_prev"], 4)
                                if unit == "%" and rec["value_cur"] is not None and rec["value_prev"] is not None
                                else None)
        else:
            rec["change_pp"] = (round(rec["share_cur"] - rec["share_prev"], 4)
                                if rec["share_cur"] is not None and rec["share_prev"] is not None else None)
        rec.update(report_period=period, indicator_code=code, indicator_name=name, section=section, unit=unit,
                   page=p, source_file=src)
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
#  Проверки
# --------------------------------------------------------------------------- #

def check_sums(rows: list, report: dict):
    by = {}
    for r in rows:
        by.setdefault(r["indicator_code"], []).append(r)
    for code, rs in by.items():
        if rs[0]["section"] == "относительные":
            continue
        tot = next((r for r in rs if r["company"] == TOTAL), None)
        comp = [r for r in rs if r["company"] != TOTAL]
        if not tot:
            report["sum"].append(f"{code}: нет строки ВСЕГО")
            continue
        for f in ("value_prev", "value_cur"):
            s = sum(r[f] or 0 for r in comp)
            t = tot[f]
            dev = (s - t) / abs(t) if t else (0 if abs(s) < 1e-9 else 1)
            item = {"code": code, "field": f, "sum": round(s, 1), "total": t, "dev_pct": round(dev * 100, 3)}
            report["sum_checked"] += 1
            if abs(dev) > TOL_SUM:
                report["sum"].append(item)


def check_ranks(rows: list, report: dict):
    by = {}
    for r in rows:
        if r["company"] != TOTAL:
            by.setdefault(r["indicator_code"], []).append(r)
    for code, rs in by.items():
        for vf, rf in (("value_prev", "rank_prev"), ("value_cur", "rank_cur")):
            seen = {}
            for r in rs:
                if r[rf] is not None:
                    seen.setdefault(r[rf], []).append(r)
            ties = 0
            for rank, grp in seen.items():
                if len(grp) > 1:
                    vals = {round(x[vf] or 0, 6) for x in grp}
                    if len(vals) > 1:
                        report["rank"].append(f"{code} {rf}={rank}: одинаковый ранг у разных значений "
                                              f"({', '.join(x['company'] for x in grp)})")
                    else:
                        ties += 1
            if ties:
                report["rank_ties"][f"{code}.{rf}"] = ties


def check_counts(rows: list, report: dict):
    by = {}
    for r in rows:
        if r["company"] != TOTAL:
            by.setdefault(r["indicator_code"], set()).add(r["company"])
    counts = {c: len(v) for c, v in by.items()}
    report["companies_per_indicator"] = counts
    common = max(set(counts.values()), key=list(counts.values()).count) if counts else 0
    report["companies_usual"] = common
    report["companies_odd"] = {c: n for c, n in counts.items() if n != common}
    allc = set().union(*by.values()) if by else set()
    for c, n in report["companies_odd"].items():
        report["companies_missing"][c] = sorted(allc - by[c])


# относительный показатель = числитель / знаменатель × k (по суммарным строкам того же рэнкинга)
# k = 100 для процентов; 1000 — обязательства в млрд сум к премиям / договорам в млн сум; 1 — выплата на претензию
RATIO_CHECKS = [
    ("payouts_to_premiums", "claims_paid", "premiums_total", 100),
    ("capital_to_assets", "total_capital", "total_assets", 100),
    ("share_capital_to_capital", "share_capital", "total_capital", 100),
    ("reserves_gross_to_premiums", "reserves_gross", "premiums_total", 100),
    ("ceded_to_premiums", "premiums_ceded", "premiums_total", 100),
    ("reserves_to_capital", "reserves_net", "total_capital", 100),
    ("reserves_to_capital", "reserves_gross", "total_capital", 100),
    ("roa", "net_profit", "total_assets", 100),
    ("roe", "net_profit", "total_capital", 100),
    ("roe_share_capital", "net_profit", "share_capital", 100),
    ("refused_to_claims", "claims_refused", "claims_received", 100),
    ("paid_to_claims", "claims_paid_count", "claims_received", 100),
    ("liabilities_to_premiums", "insurance_liabilities", "premiums_total", 1000),
    ("liabilities_per_contract", "insurance_liabilities", "contracts_active", 1000),
    ("avg_payout", "claims_paid", "claims_paid_count", 1),
]


def check_relative(rows: list, report: dict) -> dict:
    """Итог рынка относительного показателя против пересчёта по суммарным. Возвращает {код: лучшая формула}."""
    tot = {r["indicator_code"]: r for r in rows if r["company"] == TOTAL}
    best = {}
    for rel, a, b, k in RATIO_CHECKS:
        if rel not in tot or a not in tot or b not in tot:
            continue
        calc = tot[a]["value_cur"] / tot[b]["value_cur"] * k if tot[b]["value_cur"] else None
        doc = tot[rel]["value_cur"]
        dev = abs(calc - doc) if calc is not None and doc is not None else None
        rel_dev = dev / abs(doc) * 100 if dev is not None and doc else None
        report["relative"].append({"code": rel, "formula": f"{a} / {b}" + (f" × {k}" if k != 100 else " × 100"),
                                   "doc": doc, "calc": round(calc, 2) if calc is not None else None,
                                   "diff": round(dev, 2) if dev is not None else None,
                                   "diff_rel_pct": round(rel_dev, 2) if rel_dev is not None else None})
        if dev is not None and (rel not in best or dev < best[rel][1]):
            best[rel] = (f"{a} / {b}", dev)
    return best


def parse(path: Path | None = None) -> tuple:
    """→ (rows, report). Ничего не пишет."""
    path = Path(path) if path else source_file()
    if not path or not path.exists():
        raise FileNotFoundError(f"нет текста рэнкинга в {FOLDER}")
    text = path.read_text(encoding="utf-8")
    period = period_of(text)
    pg = pages(text)
    names = contents(pg)
    issues, rows = [], []
    for p in sorted(pg):
        L = pg[p]
        if TOTAL in L and "НАИМЕНОВАНИЕ" in L:
            rows.extend(parse_page(p, L, names, period, path.name, issues))
    report = {"period": period, "file": path.name, "pages": len(pg), "issues": issues, "sum": [], "sum_checked": 0,
              "rank": [], "rank_ties": {}, "companies_missing": {}, "relative": []}
    seen_codes = {r["indicator_code"] for r in rows}
    report["not_parsed"] = [c for c in CODES.values() if c not in seen_codes]
    check_sums(rows, report)
    check_ranks(rows, report)
    check_counts(rows, report)
    best = check_relative(rows, report)
    # стр. 73: содержание и заголовок расходятся (чистые или брутто резервы) — подпись по пересчёту
    if "reserves_to_capital" in best:
        f = best["reserves_to_capital"][0]
        nm = ("Страховые резервы, чистые / Совокупный капитал" if f.startswith("reserves_net")
              else "Страховые резервы, брутто / Совокупный капитал")
        for r in rows:
            if r["indicator_code"] == "reserves_to_capital":
                r["indicator_name"] = nm
        report["name_fix"] = {"reserves_to_capital": f"{nm} (подтверждено пересчётом итога рынка: {f})"}
    report["rows"] = len(rows)
    report["indicators"] = len(seen_codes)
    report["companies"] = len({r["company"] for r in rows if r["company"] != TOTAL})
    report["life"] = sorted({r["company"] for r in rows if r["is_life"]})
    report["sha256"] = sha256(path)
    return rows, report


# --------------------------------------------------------------------------- #
#  База
# --------------------------------------------------------------------------- #

COLS = ("report_period", "indicator_code", "indicator_name", "section", "company", "company_raw", "is_life",
        "value_prev", "share_prev", "rank_prev", "value_cur", "share_cur", "rank_cur", "change_pct", "change_pp",
        "growth_impact_pp", "unit", "page", "source_file", "source_sha256", "loaded_at")

FIN_DATE = "2026-07-01"          # срез company_financials: баланс на 30.06.2026 = на начало 01.07.2026 (как срезы НАПП)
FIN_NOTE = "из публичного рэнкинга, до подтверждения бухгалтерией"


def _mln(rows_by: dict, code: str):
    r = rows_by.get(code)
    if not r or r["value_cur"] is None:
        return None
    k = 1000.0 if r["unit"] == "млрд сум" else 1.0
    return r["value_cur"] * k


def inson_financials(rows: list) -> dict | None:
    """Строка company_financials из рэнкинга (суммы — в сумах, как в таблице)."""
    by = {r["indicator_code"]: r for r in rows if r["company"] == INSON}
    need = ("total_capital", "reserves_net")
    if any(_mln(by, c) is None for c in need):
        return None
    m = lambda c: None if _mln(by, c) is None else _mln(by, c) * 1e6  # noqa: E731
    return {"report_date": FIN_DATE, "own_funds": m("total_capital"), "reserves": m("reserves_net"),
            "total_capital": m("total_capital"), "share_capital": m("share_capital"),
            "reserves_gross": m("reserves_gross"), "reserves_net": m("reserves_net"), "total_assets": m("total_assets"),
            "source": f"{SOURCE_LABEL} — {FIN_NOTE}",
            "basis": ("собственные средства = совокупный капитал (стр. 21); резервы = страховые резервы чистые, за "
                      "вычетом доли перестраховщиков (стр. 24) — консервативно; брутто-резервы — для сведения"),
            "source_url": SOURCE_URL, "confirmed": 0}


def _replaceable(src) -> bool:
    """Строку company_financials можно заменить: временная, пустая или из того же рэнкинга."""
    s = (src or "").lower()
    return not s or s.startswith("временно") or "рэнкинг" in s


def upsert_financials(con, rows: list) -> str:
    fin = inson_financials(rows)
    if not fin:
        return "INSON: в рэнкинге нет совокупного капитала или чистых резервов — company_financials не тронута"
    cols = [r[1] for r in con.execute("PRAGMA table_info(company_financials)")]
    if not cols:
        return "таблицы company_financials нет"
    old = con.execute("SELECT source FROM company_financials WHERE report_date=?", (fin["report_date"],)).fetchone()
    if old and not _replaceable(old[0]):
        return (f"company_financials на {fin['report_date']}: цифры введены вручную ({old[0]}) — рэнкингом не "
                "перезаписаны")
    data = {k: v for k, v in fin.items() if k in cols}
    con.execute(f"INSERT OR REPLACE INTO company_financials ({','.join(data)}) VALUES ({','.join('?' * len(data))})",
                tuple(data.values()))
    sp = lambda x: f"{x:,.0f}".replace(",", " ")  # noqa: E731
    return (f"company_financials на {fin['report_date']}: собственные средства {sp(fin['own_funds'])} сум, "
            f"резервы {sp(fin['reserves'])} сум ({FIN_NOTE})")


def store(con, rows: list, sha: str) -> dict:
    """Пишет строки в company_rankings (замена по ключу; строки периода из прошлой версии файла — удаляются)."""
    now = datetime.now().isoformat(timespec="seconds")
    if not rows:
        return {"written": 0, "removed": 0}
    period = rows[0]["report_period"]
    keys = {(r["indicator_code"], r["company"]) for r in rows}
    old = con.execute("SELECT indicator_code, company FROM company_rankings WHERE report_period=?", (period,)).fetchall()
    removed = 0
    for code, comp in old:
        if (code, comp) not in keys:
            con.execute("DELETE FROM company_rankings WHERE report_period=? AND indicator_code=? AND company=?",
                        (period, code, comp))
            removed += 1
    sql = f"INSERT OR REPLACE INTO company_rankings ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})"
    for r in rows:
        d = dict(r, source_sha256=sha, loaded_at=now)
        con.execute(sql, tuple(d.get(c) for c in COLS))
    return {"written": len(rows), "removed": removed}


def load(con=None, path: Path | None = None, with_financials: bool = True) -> dict:
    """Разбор + запись. con — открытое соединение (транзакцию ведёт вызывающий) или None — база сервера."""
    rows, report = parse(path)
    if con is None:
        from app import db
        db.ensure_schema()
        with db.tx() as c:
            res = store(c, rows, report["sha256"])
            res["financials"] = upsert_financials(c, rows) if with_financials else None
            try:
                db.audit(c, "система: рэнкинг", "загружен рэнкинг страховщиков", report["period"],
                         {"file": report["file"], "rows": res["written"], "indicators": report["indicators"],
                          "financials": res["financials"]})
                db.reference_changed(c)        # лимит 20 % на риск в расчёте берёт новые собственные средства
            except Exception:
                pass
    else:
        res = store(con, rows, report["sha256"])
        res["financials"] = upsert_financials(con, rows) if with_financials else None
    report.update(res)
    return report


def loaded_sha(con, period: str = None) -> set:
    try:
        sql = "SELECT DISTINCT source_sha256 FROM company_rankings" + (" WHERE report_period=?" if period else "")
        return {r[0] for r in con.execute(sql, (period,) if period else ())}
    except sqlite3.OperationalError:
        return set()


def needs_load(con, path: Path | None = None) -> bool:
    """Таблица пуста или текст рэнкинга изменился (другой sha256)."""
    path = Path(path) if path else source_file()
    if not path or not path.exists():
        return False
    return sha256(path) not in loaded_sha(con)


def export_csv(rows: list, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [c for c in COLS if c not in ("source_sha256", "loaded_at")]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([("" if r.get(c) is None else str(r.get(c)).replace(".", ",") if isinstance(r.get(c), float)
                         else r.get(c)) for c in cols])


def print_report(rep: dict):
    print(f"Рэнкинг: {rep['file']} — период {rep['period']}, страниц {rep['pages']}")
    print(f"  разобрано строк: {rep['rows']}, показателей: {rep['indicators']} из {len(CODES)}, "
          f"компаний: {rep['companies']} (жизнь: {len(rep['life'])})")
    print(f"  сверка «сумма компаний = ВСЕГО» (±1 %): проверено {rep['sum_checked']}, расхождений {len(rep['sum'])}")
    for s in rep["sum"]:
        print("    РАСХОЖДЕНИЕ", s)
    print(f"  ранги: противоречий {len(rep['rank'])}; общие ранги у равных значений — {len(rep['rank_ties'])} столбцов")
    for s in rep["rank"]:
        print("    РАНГ", s)
    print(f"  компаний на странице обычно {rep['companies_usual']}; иначе: {rep['companies_odd'] or 'нет'}")
    for c, miss in rep["companies_missing"].items():
        print(f"    {c}: нет строк {miss}")
    for r in rep["relative"]:
        print(f"  пересчёт итога {r['code']} = {r['formula']}: документ {r['doc']}, пересчёт {r['calc']}, "
              f"разница {r['diff']} ({r['diff_rel_pct']} % от значения)")
    for k, v in (rep.get("name_fix") or {}).items():
        print(f"  подпись {k}: {v}")
    if rep["not_parsed"]:
        print("  НЕ РАЗОБРАНО:", rep["not_parsed"])
    for s in rep["issues"]:
        print("  ПРОПУСК", s)
    if "written" in rep:
        print(f"  записано в базу: {rep['written']}, удалено устаревших: {rep['removed']}")
        print(f"  {rep.get('financials')}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Рэнкинг страховщиков snsratings.uz → company_rankings")
    ap.add_argument("--load", action="store_true", help="загрузить в базу")
    ap.add_argument("--db", help="путь к базе (по умолчанию — база сервера)")
    ap.add_argument("--file", help="текст рэнкинга (по умолчанию — последний .txt в папке)")
    ap.add_argument("--csv", help="куда выгрузить таблицу (CSV, ;)")
    ap.add_argument("--json", help="куда записать отчёт о сходимости (JSON)")
    a = ap.parse_args(argv)
    path = Path(a.file) if a.file else None
    if a.load:
        if a.db:
            from app import db
            db.DB_PATH = Path(a.db)
        rep = load(path=path)
        rows, _ = parse(path)
    else:
        rows, rep = parse(path)
    print_report(rep)
    out = Path(a.csv) if a.csv else ROOT / "docs" / f"Рэнкинг snsratings — {rep['period']}.csv"
    export_csv(rows, out)
    print("  таблица:", out)
    if a.json:
        Path(a.json).write_text(json.dumps({k: v for k, v in rep.items()}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return rep


if __name__ == "__main__":
    main()
