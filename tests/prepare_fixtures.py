"""Подготовка воспроизводимых тестовых данных; только внутри изолированной копии.

Запускается tools/test_all.py после сборки справочников. Данные для stat_series
синтетические; они не являются статистикой Узбекистана и не поставляются серверу.
"""
from pathlib import Path
from html import escape
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    if ROOT.parent.parent.name != "test-runs":
        raise RuntimeError("Подготовка разрешена только в копии tools/test_all.py")
    from app import db
    from app.infrastructure.lifecycle import initialize_storage
    from app.config import Settings
    initialize_storage(Settings.from_env())
    # Большинство существующих тестов фиксируют этот учебный баланс.
    with db.tx() as con:
        con.execute("DELETE FROM company_financials")
        con.execute("INSERT INTO company_financials (report_date, own_funds, reserves, source) VALUES (?,?,?,?)",
                    ("2026-06-30", 180e9, 240e9, "временно, до данных бухгалтерии (тест)"))
        region = "region:TOSHKENT SHAHRI"
        other = "region:TOSHKENT"
        for dataset, unit, value in (
            ("population", "тысяч человек", 3000), ("grp", "млрд сум", 100000),
            ("grp_per_capita", "тысяч сум", 33000), ("house_price_index", "%", 110),
            ("construction_price_index_y", "%", 108), ("housing_fund_area", "кв. м", 10000000),
        ):
            for key, ratio in (("total", 10), (region, 1), (other, 0.8)):
                con.execute("INSERT OR REPLACE INTO stat_series (source,dataset_id,key,region,period,value,unit,fetched_at,url) "
                            "VALUES ('stat.uz',?,?,?,?,?,?,'2026-10-01','https://stat.uz/')",
                            (dataset, "1700" if key == "total" else key,
                             "" if dataset == "construction_price_index_y" else key,
                             "2025", value * ratio if 'index' not in dataset else value, unit))
        for region, area, share in (("total", 1e8, .533629), (region, 1e7, .45), (other, 8e6, .499509)):
            for dataset, value in (("housing_walls_adobe", area * share * .6),
                                   ("housing_walls_raw_brick", area * share * .4)):
                con.execute("INSERT OR REPLACE INTO stat_series (source,dataset_id,key,region,period,value,unit,fetched_at,url) "
                            "VALUES ('stat.uz',?,?,?,'2025',?,'кв. м','2026-10-01','https://stat.uz/')",
                            (dataset, region, region, value / 1000))
        for dataset, ratio in (("road_accidents", 1.735), ("thefts", 1.215), ("crimes_total", 1.735)):
            for region, population in (("total", 30000), ("region:TOSHKENT SHAHRI", 3000), (other, 2400)):
                url = "https://data.egov.uz/rus/data/6114e27e114fbfdc20c354cc" if dataset == "road_accidents" else "https://stat.uz/"
                con.execute("INSERT OR REPLACE INTO stat_series (source,dataset_id,key,region,period,value,unit,fetched_at,url) "
                            "VALUES ('stat.uz',?,?,?,'2025',?,'случаев','2026-10-01',?)",
                            (dataset, region, region, population * (1 if region == "total" else ratio), url))
        con.execute("INSERT OR REPLACE INTO stat_series (source,dataset_id,key,region,period,value,unit,fetched_at,url) "
                    "VALUES ('data.egov.uz','emergencies','Jami','','2026-Q2',32,'случаев','2026-10-01','https://data.egov.uz/')")
    create_documents()
    create_exchange_html()
    create_ui_responses()
    db.close_pool()


def create_documents():
    import test_act as cases
    target = ROOT / "tests/fixtures"
    (target / "contract.docx").write_bytes(cases.docx_bytes([
        "ДОГОВОР СТРАХОВАНИЯ ИМУЩЕСТВА № 15/2026",
        "г. Ташкент    21 сентября 2026 года",
        "Страхователь: ООО «Тестовый склад»",
        "Объект страхования: склад готовой продукции",
        "Адрес объекта: г. Ташкент, Юнусабадский район",
        "Конструкция: кирпич", "Год постройки: 2012",
        "Страховая сумма: 4 200 000 000 сум",
        "Страховая стоимость: 5 000 000 000 сум",
        "Срок страхования: 12 месяцев",
    ]))
    (target / "equipment.docx").write_bytes(cases.docx_table(cases.BR_SAMPLE2))


def create_exchange_html():
    headers = ["Shartnoma raqami", "Nomi", "Lot", "O'lchov birligi", "Bazis narxi", "Omborning"]
    rows = [
        ["106", "Дизельное топливо ЭКО-Л", "60", "тонна", "870000000,0000", "Навои"],
        ["265", "Мука пшеничная", "100", "килограмм", "427000", "Ташкент"],
        ["102", "Печное топливо", "1", "тонна", "1000000", "Ташкент"],
        ["205", "Печное топливо", "1", "тонна", "1000000", "Ташкент"],
        ["245", "Масло моторное", "1", "тонна", "1000000", "Ташкент"],
        ["163", "Сода кальцинированная", "1", "тонна", "1000000", "Ташкент"],
        ["275", "Арматура стальная", "1", "тонна", "1000000", "Ташкент"],
        ["278", "Арматура стальная", "1", "тонна", "1000000", "Ташкент"],
        ["279", "Арматура стальная", "1", "тонна", "1000000", "Ташкент"],
        ["300", "Прочий товар", "1", "штука", "1000", "Ташкент"],
    ]
    html = '<!-- Synthetic offline fixture -->\n<table><thead><tr>'
    html += ''.join('<th>' + escape(v) + '</th>' for v in headers) + '</tr></thead><tbody>'
    html += ''.join('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in row) + '</tr>' for row in rows)
    html += '</tbody></table><p>148654 dan 1 dan 10 gacha yozuvlar aks ettirildi</p>'
    (ROOT / "tests/fixtures/exchange.html").write_text(html, encoding="utf-8")


def create_ui_responses():
    import test_act as cases
    from app import guest, llm
    llm.chat_raw = cases.fake_chat_raw
    llm._post = cases.no_network
    crane, _ = cases.check_crane()
    if cases.failed:
        raise RuntimeError("Контрольный пример автокрана не прошёл")
    (ROOT / "tests/fixtures/act_demo.json").write_text(json.dumps(crane, ensure_ascii=False), encoding="utf-8")
    cases.model_on(False)
    status, branch = cases.upload([("equipment.docx", cases.DOCX_MIME,
                                    (ROOT / "tests/fixtures/equipment.docx").read_bytes())], {"lang": "ru"})
    if status != 200:
        raise RuntimeError("Не разобран учебный запрос филиала")
    options = [
        ("act_demo_equipment", cases.EQ_MUST, {**cases.EQ_OPT, "request": cases.br_request(branch)}),
        ("act_demo_multi", cases.PT_MUST, {**cases.PT_OPT, "parts": [cases.PT_CAR, cases.PT_CREDIT],
                                         "parts_confirmed": True, "parts_object_mode": "different"}),
    ]
    for name, must, optional in options:
        guest.reset()
        cases.act.reset_limits()
        status, result = cases.call("POST", "/act/make", {"lang": "ru", "must": must, "optional": optional})
        if status != 200:
            raise RuntimeError(f"Fixture {name}: HTTP {status}")
        (ROOT / "tests/fixtures" / (name + ".json")).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
