"""
Самотест адаптеров источников объявлений. Запуск:  python tests/test_valuation_sources.py
Офлайн: сеть не используется, проверяются только разбор цен, фильтр по дате и статистика.
Без pytest — обычные assert, чтобы не ставить лишнего.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import valuation_sources as vs  # noqa: E402


def test_parse_price():
    """Цены с сайтов: у.е. и сумы, с неразрывными пробелами и тильдой."""
    assert vs.parse_price("~13 954 y.e.") == (13954.0, "USD")
    assert vs.parse_price("Цена: 108 000 у.е.") == (108000.0, "USD")
    assert vs.parse_price("850 000 000 сум") == (850000000.0, "UZS")
    assert vs.parse_price("1 200 000 so'm") == (1200000.0, "UZS")
    assert vs.parse_price("$45 500") == (45500.0, "USD")
    assert vs.parse_price("договорная") == (None, "")
    assert vs.parse_price("") == (None, "")
    print("ok  parse_price")


def test_to_uzs():
    """Без курса долларовая цена НЕ пересчитывается — выдумывать курс нельзя."""
    assert vs.to_uzs(1_000_000, "UZS", None) == 1_000_000
    assert vs.to_uzs(10_000, "USD", None) is None
    assert vs.to_uzs(10_000, "USD", 12_000) == 120_000_000
    assert vs.to_uzs(None, "USD", 12_000) is None
    print("ok  to_uzs")


def test_window():
    """Окно 6 месяцев от даты заключения; без даты объявление не берётся."""
    as_of = date(2026, 9, 20)
    assert vs.window_start(as_of) == date(2026, 3, 20)
    assert vs.window_start(date(2026, 8, 31), 6) == date(2026, 2, 28)
    assert vs.in_window(date(2026, 9, 19), as_of) is True
    assert vs.in_window(date(2026, 3, 20), as_of) is True
    assert vs.in_window(date(2026, 3, 19), as_of) is False
    assert vs.in_window(date(2026, 9, 21), as_of) is False   # будущее не берём
    assert vs.in_window(None, as_of) is False                # даты нет — не берём
    print("ok  in_window")


def test_parse_iso_date():
    assert vs.parse_iso_date("2026-09-20T12:19:00+05:00") == date(2026, 9, 20)
    assert vs.parse_iso_date("2026-09-15") == date(2026, 9, 15)
    assert vs.parse_iso_date("вчера") is None
    assert vs.parse_iso_date("") is None
    print("ok  parse_iso_date")


def test_quantiles():
    """Квартили линейной интерполяцией на подготовленном ряду."""
    q1, med, q3 = vs.quantiles([1, 2, 3, 4, 5])
    assert (q1, med, q3) == (2.0, 3.0, 4.0)
    q1, med, q3 = vs.quantiles([10, 20, 30, 40])
    assert (q1, med, q3) == (17.5, 25.0, 32.5)
    assert vs.quantiles([]) == (None, None, None)
    assert vs.quantiles([7]) == (7.0, 7.0, 7.0)
    print("ok  quantiles")


def test_finish_offline():
    """Сборка результата на подготовленных объявлениях, без сети."""
    rate = 12_000.0
    rows = []
    for price, cur in [(10_000, "USD"), (12_000, "USD"), (14_000, "USD"), (16_000, "USD")]:
        rows.append(vs.Sample(title="Chevrolet Cobalt", price=price, currency=cur,
                              price_uzs=vs.to_uzs(price, cur, rate),
                              published="2026-08-01", url="https://avtoelon.uz/a/show/1"))
    res = vs.SourceResult(source="avtoelon.uz", status=vs.STATUS_DOWN)
    vs._finish(res, rows, rate)
    assert res.status == vs.STATUS_OK
    assert res.ads_count == 4
    assert res.median == 13_000 * rate
    assert res.q1 == 11_500 * rate and res.q3 == 14_500 * rate
    assert len(res.samples) == 4

    empty = vs.SourceResult(source="avtoelon.uz", status=vs.STATUS_DOWN)
    vs._finish(empty, [], rate)
    assert empty.status == vs.STATUS_EMPTY and empty.median is None

    # те же объявления без курса: статистика не считается, честная оговорка есть
    rows_nr = [vs.Sample(title=r.title, price=r.price, currency=r.currency,
                         price_uzs=None, published=r.published, url=r.url) for r in rows]
    nr = vs.SourceResult(source="avtoelon.uz", status=vs.STATUS_DOWN)
    vs._finish(nr, rows_nr, None)
    assert nr.median is None and nr.prices == [] and nr.prices_usd == [10_000, 12_000, 14_000, 16_000]
    assert any("курс" in x for x in nr.limitations)
    print("ok  _finish")


def test_parse_avtoelon_offline():
    """Разбор карточки avtoelon по сохранённому фрагменту выдачи (без сети)."""
    html = (
        '<script type="text/javascript">listing.items.push('
        '{"attributes":{"model":"Cobalt","brand":"Chevrolet"},'
        '"lastUpdate":"2026-09-20T12:19:00+05:00","status":"live","unitPrice":13954,'
        '"url":"https:\\/\\/avtoelon.uz\\/a\\/show\\/7333875"});</script>'
        '<div data-id="7333875" class="row list-item a-elem">'
        '<a href="/a/show/7333875" class="js__advert-link">Chevrolet Cobalt, 4 позиция</a>'
        '<span class="price"><span>Цена: </span>~13&nbsp;954&nbsp;y.e.</span>'
        '<span class="year">2026&nbsp;г.,</span></div>'
        # второе объявление — старое, должно отсеяться окном 6 месяцев
        '<script type="text/javascript">listing.items.push('
        '{"attributes":{"model":"Cobalt","brand":"Chevrolet"},'
        '"lastUpdate":"2025-01-10T09:00:00+05:00","unitPrice":9000,'
        '"url":"https:\\/\\/avtoelon.uz\\/a\\/show\\/1111111"});</script>'
        '<div data-id="1111111" class="row list-item a-elem">'
        '<a href="/a/show/1111111" class="js__advert-link">Chevrolet Cobalt, 2 позиция</a>'
        '<span class="price"><span>Цена: </span>9&nbsp;000&nbsp;y.e.</span>'
        '<span class="year">2019&nbsp;г.,</span></div>'
    )
    res = vs.SourceResult(source="avtoelon.uz", status=vs.STATUS_DOWN)
    rows = vs._parse_avtoelon(html, date(2026, 9, 20), 12_000.0, {}, res)
    assert len(rows) == 1, rows
    assert rows[0].price == 13954.0 and rows[0].currency == "USD"
    assert rows[0].price_uzs == 13954.0 * 12_000
    assert rows[0].published == "2026-09-20"

    # фильтр по году: 2019 ± 1 — свежее объявление 2026 года не подходит
    res2 = vs.SourceResult(source="avtoelon.uz", status=vs.STATUS_DOWN)
    rows2 = vs._parse_avtoelon(html, date(2026, 9, 20), None, {"год": 2019}, res2)
    assert rows2 == []
    print("ok  _parse_avtoelon")


def test_statuses_defined():
    """Статусы источника — ровно те четыре, что записаны в контракте."""
    assert vs.STATUS_OK == "ok"
    assert vs.STATUS_EMPTY == "нет объявлений"
    assert vs.STATUS_DOWN == "источник недоступен"
    assert vs.STATUS_ROBOTS == "запрещено robots.txt"
    assert set(vs.ADAPTERS) == {"avtoelon.uz", "olx.uz", "uybor.uz", "joymee.uz"}
    assert vs.MIN_DELAY_SEC >= 2.0
    assert "INSON" in vs.USER_AGENT and "@" in vs.USER_AGENT
    print("ok  контракт адаптеров")


if __name__ == "__main__":
    test_parse_price()
    test_to_uzs()
    test_window()
    test_parse_iso_date()
    test_quantiles()
    test_finish_offline()
    test_parse_avtoelon_offline()
    test_statuses_defined()
    print("\nВсе проверки пройдены (офлайн, без обращения к сайтам).")
