"""
Самотест слоя загрузки открытых данных статистики. Запуск:
    python tests/test_stat_sources.py
Офлайн: сеть не используется. Разбор проверяется на сохранённом образце ответа
api.siat.stat.uz (tests/data/stat_population_sample.json) и на образце ответа
data.egov.uz, записанном прямо в тесте. Запись в базу проверяется на временном
файле — рабочая база data/surveyor.db не трогается.
Без pytest — обычные assert, чтобы не ставить лишнего.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import stat_sources as ss  # noqa: E402

SAMPLE = ROOT / "tests" / "data" / "stat_population_sample.json"

# Образец ответа data.egov.uz (набор МЧС «Статистика произошедших чрезвычайных ситуаций»)
EGOV_SAMPLE = json.dumps([
    {"ID": "1", "Favquloddavaziyatlarturlari": "Texnogen xususiyatli favqulodda vaziyatlar",
     "2025yilIIchorak": "32", "2026yilIIchorak": "26"},
    {"ID": "2", "Favquloddavaziyatlarturlari": "Tabiiy xususiyatli favqulodda vaziyatlar",
     "2025yilIIchorak": "12", "2026yilIIchorak": "6"},
    {"ID": "3", "Favquloddavaziyatlarturlari": "Jami",
     "2025yilIIchorak": "44", "2026yilIIchorak": "32"},
], ensure_ascii=False)


def test_norm_period():
    """Периоды: год, квартал, месяц; кириллическая «М» приводится к латинской."""
    assert ss.norm_period("2025") == "2025"
    assert ss.norm_period("2026-Q2") == "2026-Q2"
    assert ss.norm_period("2026-M08") == "2026-M08"
    assert ss.norm_period("2010-М01") == "2010-M01"   # кириллическая М у старых столбцов
    assert ss.norm_period("Code") is None
    assert ss.norm_period("Klassifikator_ru") is None
    assert ss.norm_period("") is None
    print("ok  norm_period")


def test_region_key():
    """СОАТО → ключи region:* из market_stats; районы и чужие коды — без разреза."""
    assert ss.region_key("1700") == "total"
    assert ss.region_key("1703") == "region:ANDIJON"
    assert ss.region_key("1726") == "region:TOSHKENT SHAHRI"
    assert ss.region_key("1727") == "region:TOSHKENT"
    assert ss.region_key("1735") == "region:QORAQALPOG'ISTON RESPUBLIKASI"
    assert ss.region_key("1703202") == ""                  # район — не разрез
    assert ss.region_key("9999") == ""
    # все ключи разреза должны иметь русское название для показа заказчику
    for k in ss.REGIONS.values():
        assert k in ss.REGION_NAMES_RU
    print("ok  region_key")


def test_catalog_is_honest():
    """У каждого набора в реестре — страница источника, единица и ограничения."""
    for d in ss.DATASETS.values():
        assert d["page"].startswith("https://"), d["id"]
        assert d["data_url"].startswith("https://"), d["id"]
        assert d["unit"], d["id"]
        assert d["purpose"], d["id"]
        # набор без разреза регионов обязан сказать об этом в ограничениях
        if not d["regions"]:
            assert d["limits"], d["id"]
    print("ok  catalog (%d наборов)" % len(ss.DATASETS))


def test_parse_siat():
    """Разбор ответа api.siat.stat.uz на сохранённом образце."""
    ds = ss.DATASETS["population"]
    recs, passport = ss.parse_siat(SAMPLE.read_text(encoding="utf-8"), ds)
    assert passport["unit"] == "тысяч человек"
    assert passport["period"] == "на начало года"
    # в образце 4 региональные строки и 1 районная; районная отбрасывается
    keys = {r["key"] for r in recs}
    assert keys == {"1700", "1735", "1703", "1706"}, keys
    assert {r["period"] for r in recs} == {"2024", "2025", "2026"}
    assert len(recs) == 12
    by = {(r["region"], r["period"]): r["value"] for r in recs}
    assert by[("total", "2026")] == 38236.7
    assert by[("region:ANDIJON", "2026")] == 3521.8
    for r in recs:
        assert r["url"] == ds["page"]                      # ссылка на источник у каждой строки
        assert r["unit"] == "тысяч человек"
    print("ok  parse_siat")


def test_parse_egov():
    """Разбор ответа data.egov.uz: имена полей «2026yilIIchorak» → период 2026-Q2."""
    ds = ss.DATASETS["emergencies"]
    recs, _ = ss.parse_egov(EGOV_SAMPLE, ds)
    assert len(recs) == 6
    assert {r["period"] for r in recs} == {"2025-Q2", "2026-Q2"}
    jami = {r["period"]: r["value"] for r in recs if r["key"] == "Jami"}
    assert jami == {"2025-Q2": 44.0, "2026-Q2": 32.0}
    assert all(r["region"] == "" for r in recs)            # разреза регионов в наборе нет
    assert all(r["url"] == ds["page"] for r in recs)
    print("ok  parse_egov")


def test_save_keeps_history():
    """
    Повторная загрузка не плодит дубли; новый период добавляется, старый остаётся;
    изменённое значение уходит в журнал пересчётов.
    """
    ds = ss.DATASETS["population"]
    recs, _ = ss.parse_siat(SAMPLE.read_text(encoding="utf-8"), ds)
    for r in recs:
        r["fetched_at"] = "2026-09-20T12:00:00+05:00"
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        con = sqlite3.connect(str(db))
        schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
        start = schema.index("CREATE TABLE IF NOT EXISTS stat_series")
        con.executescript(schema[start:])
        con.close()

        assert ss.save(recs, db)["вставлено"] == 12
        # второй прогон тех же данных — ни одной новой строки
        r2 = ss.save(recs, db)
        assert r2 == {"вставлено": 0, "изменено": 0, "без изменений": 12, "всего": 12}, r2

        # новый период добавляется, старые остаются
        new = dict(recs[0], period="2027", value=1.0)
        assert ss.save([new], db)["вставлено"] == 1

        # пересчёт значения: в ряду остаётся новое, старое — в журнале
        changed = dict(recs[0], value=(recs[0]["value"] or 0) + 100.0,
                       fetched_at="2026-12-01T10:00:00+05:00")
        assert ss.save([changed], db)["изменено"] == 1
        con = sqlite3.connect(str(db))
        assert con.execute("SELECT COUNT(*) FROM stat_series").fetchone()[0] == 13
        rev = con.execute("SELECT old_value, new_value FROM stat_series_revisions").fetchone()
        assert rev == (recs[0]["value"], changed["value"]), rev
        cur = con.execute(
            "SELECT value FROM stat_series WHERE dataset_id=? AND key=? AND region=? AND period=?",
            (recs[0]["dataset_id"], recs[0]["key"], recs[0]["region"], recs[0]["period"])).fetchone()
        assert cur[0] == changed["value"]
        # ссылка на источник есть у каждой строки — правило заказчика
        assert con.execute("SELECT COUNT(*) FROM stat_series WHERE url IS NULL OR url=''").fetchone()[0] == 0
        con.close()
    print("ok  save (история сохраняется, дублей нет)")


def test_fetch_unknown_dataset():
    """Незарегистрированный набор — честный статус, а не исключение."""
    recs, st = ss.fetch("нет-такого-набора")
    assert recs == []
    assert st["status"] == ss.STATUS_DOWN
    assert "не зарегистрирован" in st["reason"]
    print("ok  fetch (неизвестный набор)")


# Образец ответа api.siat.stat.uz в формате наборов 1256–1260 и 1243 (строки и значения сняты 02.10.2026 с
# sdmx_data_1258.json и sdmx_data_1243.json: республика, Ташкентская область и один район — район отбрасывается)
WALLS_SAMPLE = json.dumps([{
    "metadata": [{"name_ru": "Имя индикатора", "value_ru": "Распределение жилищного фонда по материалу стен: "
                                                            "крупно-панельные и железобетонные"},
                 {"name_ru": "Периодичность", "value_ru": "ежегодный"},
                 {"name_ru": "Единица измерения", "value_ru": "на конец года, тысяч кв. м"},
                 {"name_ru": "Дата последнего изменения", "value_ru": "2026-06-30"}],
    "data": [{"Code": "1700", "Klassifikator_ru": "Республика Узбекистан", "2024": 120.0, "2025": 121.5},
             {"Code": "1727", "Klassifikator_ru": "Ташкентская область", "2024": 3566.6, "2025": 3566.7},
             {"Code": "1727206", "Klassifikator_ru": "район", "2024": 1.0, "2025": 1.0}]}], ensure_ascii=False)
GAS_SAMPLE = json.dumps([{
    "metadata": [{"name_ru": "Единица измерения", "value_ru": "на конец года, в % к общему числу квартир (домов)"}],
    "data": [{"Code": "1700", "2024": 56.7, "2025": 57.6}, {"Code": "1727", "2024": 91.0, "2025": 91.9}]}],
    ensure_ascii=False)


def test_wall_fund_and_gas():
    """
    Наборы класса 8 для фона факторов «конструкция» и «отопление» (02.10.2026): пять наборов материала стен
    (1256–1260) и обеспеченность газом (1243) — в реестре, по регионам, класс 8, ограничение «коэффициент не
    калибруется»; разбор образца того же формата: регионы есть, районы отброшены, единица — у источника.
    """
    want = {"housing_fund_by_walls_brick": "1256", "housing_walls_raw_brick": "1257",
            "housing_walls_panel_rc": "1258", "housing_walls_other": "1259", "housing_walls_adobe": "1260",
            "gas_supply_share": "1243"}
    for ds_id, src in want.items():
        d = ss.DATASETS[ds_id]
        assert d["src_id"] == src and d["source"] == ss.SOURCE_STAT, ds_id
        assert d["regions"] is True and d["class_codes"] == ["8"], ds_id
        assert d["page"] == "https://stat.uz/ru/ofitsialnaya-statistika/environment", ds_id
        assert d["data_url"] == ss.SIAT_JSON % src, ds_id
        assert any("конкретного объекта" in x for x in d["limits"]), ds_id
        assert any("калиб" in x for x in d["limits"]), ds_id
        assert ds_id in ss.datasets_for_class("8"), ds_id
        assert ds_id in {c["id"] for c in ss.catalog()}, ds_id
    recs, passport = ss.parse_siat(WALLS_SAMPLE, ss.DATASETS["housing_walls_panel_rc"])
    assert passport["unit"] == "на конец года, тысяч кв. м"
    by = {(r["region"], r["period"]): r["value"] for r in recs}
    assert by == {("total", "2024"): 120.0, ("total", "2025"): 121.5,
                  ("region:TOSHKENT", "2024"): 3566.6, ("region:TOSHKENT", "2025"): 3566.7}, by
    assert all(r["dataset_id"] == "housing_walls_panel_rc" and r["url"] == ss.DATASETS["housing_walls_panel_rc"]["page"]
               for r in recs)
    recs, passport = ss.parse_siat(GAS_SAMPLE, ss.DATASETS["gas_supply_share"])
    assert passport["unit"].startswith("на конец года, в %")
    assert {(r["region"], r["period"]): r["value"] for r in recs}[("region:TOSHKENT", "2025")] == 91.9
    print("ok  наборы фона класса 8 (материал стен 1256–1260, газ 1243)")


if __name__ == "__main__":
    test_norm_period()
    test_region_key()
    test_catalog_is_honest()
    test_parse_siat()
    test_parse_egov()
    test_save_keeps_history()
    test_fetch_unknown_dataset()
    test_wall_fund_and_gas()
    print("\nВсе проверки stat_sources пройдены.")
