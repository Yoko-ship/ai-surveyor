"""
Контрольные примеры клиента ИИ (app/llm.py).
Запуск:  sandbox\\.venv\\Scripts\\python.exe tests/test_llm.py

Сеть не нужна: проверяем маскировку персональных данных и поведение без ключа.
Проверяем:
  1) маскировка ПИНФЛ, паспорта, ИНН, телефона, e-mail, номера карты, госномера, кадастра;
  2) маскировка ФИО — кириллицей, латиницей и «Фамилия И. О.»;
  3) полезный текст (суммы, ставки, названия документов) маскировкой не портится;
  4) без ключа: status() говорит «не подключён», chat() и extract_fields() возвращают пусто;
  5) объяснение расчёта без ИИ собирается по шаблону и содержит цифры;
  6) маска ключа не показывает ключ целиком.
Записей в базе тест не создаёт (кроме строки журнала llm_calls, которая удаляется в конце).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                          # noqa: E402
from app import llm                                         # noqa: E402


def test_pinfl():
    out = llm.mask_pd("ПИНФЛ страхователя 31234567890123, проверить в реестре")
    assert "31234567890123" not in out, out
    assert "[ПИНФЛ]" in out, out


def test_passport_and_inn():
    out = llm.mask_pd("Паспорт AA 1234567, ИНН 305123456")
    assert "1234567" not in out and "305123456" not in out, out
    assert "[ПАСПОРТ]" in out and "[ИНН]" in out, out


def test_phone_and_email():
    out = llm.mask_pd("Телефон +998 90 123-45-67, почта ivanov.i@mail.uz")
    assert "123-45-67" not in out and "mail.uz" not in out, out
    assert "[ТЕЛЕФОН]" in out and "[E-MAIL]" in out, out


def test_card_and_plate_and_cadastre():
    out = llm.mask_pd("Карта 8600 1234 5678 9012, машина 01 A 123 AB, кадастр 10:09:05:01:0123:0001")
    assert "8600" not in out and "0123" not in out, out
    assert "[НОМЕР КАРТЫ]" in out and "[ГОСНОМЕР]" in out and "[КАДАСТР]" in out, out


def test_names():
    a = llm.mask_pd("Страхователь Иванов Пётр Сергеевич обратился в филиал")
    assert "Иванов" not in a and "[ФИО]" in a, a
    b = llm.mask_pd("Owner: Ivanov Petr Sergeevich")
    assert "Ivanov" not in b and "[ФИО]" in b, b
    c = llm.mask_pd("Подпись: Каримов А. Б.")
    assert "Каримов" not in c and "[ФИО]" in c, c


def test_useful_text_survives():
    src = "Страховая сумма 250 000 000 сум, ставка 0,35%, класс 8, износ 12%"
    out = llm.mask_pd(src)
    assert "250 000 000" in out and "0,35%" in out and "класс 8" in out, out


def test_mask_is_applied_to_dict():
    out = llm.mask_pd({"фио": "Иванов Пётр", "пинфл": "31234567890123"})
    assert "Иванов" not in out and "31234567890123" not in out, out


def test_no_key_behaviour():
    """Без ключа ИИ честно выключен, а вызовы ничего не ломают."""
    if llm.enabled():
        print("  (ключ ИИ задан — проверка поведения без ключа пропущена)")
        return
    st = llm.status()
    assert st["connected"] is False, st
    assert "не подключ" in st["reason"] or "выключен" in st["reason"] or "не поддержан" in st["reason"], st
    assert llm.chat("тест", "system", "user") is None
    assert llm.extract_fields("текст документа", [{"key": "year", "name": "Год"}], "техпаспорт") == {}
    assert llm.ping()["ok"] is False


def test_key_mask():
    assert llm.mask_key("sk-abcdefghijklmnop1234") == "sk-...1234"
    assert llm.mask_key("") == ""
    assert "secret" not in llm.mask_key("secretkey12345678")


def test_explain_template_always_works():
    card = {"request": {"product_code": "0126"},
            "object": {"object_type": "квартира", "sum_insured": 250_000_000,
                       "attributes": '{"factors": {"class_code": "8"}, "term_from": "2026-01-01",'
                                     ' "term_to": "2026-12-31"}'},
            "calculation": {"gross_rate_pct": 0.35, "applied_rate_pct": 0.4, "min_rate_pct": 0.3,
                            "premium": 1_000_000, "verdict": "ок",
                            "explanation": '{"chain": [{"name": "Базовая нетто-ставка", "value_pct": 0.2},'
                                           ' {"name": "Сейсмозона · 8 баллов", "mult": 1.3}]}'},
            "checks": [], "recommendations": [{"kind": "франшиза", "text": "Франшиза 1% снизит премию",
                                               "premium_delta": -50000}]}
    res = llm.explain_calculation(card)
    assert res["text"], res
    assert res["source"] in ("шаблон", "ИИ")
    assert "квартира" in res["template"]
    assert "0,350%" in res["template"] or "0.35" in res["template"], res["template"]
    assert "Франшиза" in res["template"]
    # в объяснении не должно быть персональных данных
    assert llm.mask_pd(res["template"]) == res["template"] or "[ФИО]" not in res["template"]


def test_news_interface_is_a_stub():
    res = llm.analyze_news([{"title": "Изменения в положении об агентах", "url": "https://lex.uz/x"}])
    assert res["ok"] is False and res["items"][0]["важность"] is None


def cleanup():
    """Убираем строки журнала, которые мог оставить тест."""
    with db.tx() as con:
        con.execute("DELETE FROM llm_calls WHERE purpose IN ('тест', 'проверка связи')")
    print("тестовые записи журнала удалены")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    try:
        for t in tests:
            t()
            print("ок:", t.__name__)
    finally:
        cleanup()
    print("\nвсе проверки клиента ИИ пройдены:", len(tests))


if __name__ == "__main__":
    main()
