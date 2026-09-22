# -*- coding: utf-8 -*-
"""
Проверка справочника ответов docs/Специалист — FAQ.json (до 22.09.2026 — «Юрист — FAQ.json»).

Две части: нормативная (цитаты сверяются с текстами актов) и практическая
(kind="практика": цитат нет, вместо них пометка basis «заметка проекта · практика компании / CII»).

Ничего не меняет: только читает JSON и тексты актов в library/.
Запуск: PYTHONIOENCODING=utf-8 python tests/test_faq.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library" / "01_Законодательство"
FAQ = ROOT / "docs" / "Специалист — FAQ.json"

sys.path.insert(0, str(ROOT / "tools"))
import faq_build  # noqa: E402

errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def main():
    data = json.loads(FAQ.read_text(encoding="utf-8"))
    items = data["items"]
    norms = [i for i in items if i.get("kind", "норма") == "норма"]
    practice = [i for i in items if i.get("kind") == "практика"]
    check(50 <= len(norms) <= 70, f"нормативных вопросов должно быть 50–70, а их {len(norms)}")
    check(len(practice) >= 40, f"практических вопросов должно быть 40 и больше, а их {len(practice)}")
    check(len({i["id"] for i in items}) == len(items), "есть повторяющиеся id вопросов")

    # тексты актов, в которых ищем цитаты. Берём ИСХОДНЫЙ текст файла (схлопнуты пробелы,
    # убраны только кнопки страницы lex.uz) — иначе проверка «дословно» ничего не проверяет.
    texts = {rel: faq_build.raw(rel) for rel in faq_build.F.values()}
    # и отдельно — весь состав библиотеки: цитата может быть взята из файла, не указанного в F
    for p in LIB.rglob("*.txt"):
        texts.setdefault(str(p), re.sub(r"\s+", " ", re.sub(faq_build.UI, " ",
                                                            p.read_text(encoding="utf-8", errors="ignore"))))

    # практические ответы: нормы нет, зато обязателен источник-пометка на трёх языках
    for i in practice:
        check(not i["citations"], f"{i['id']}: практический вопрос не должен ссылаться на норму")
        for lang in ("ru", "uz", "en"):
            check(bool((i.get("basis") or {}).get(lang)), f"{i['id']}: нет пометки источника на {lang}")
        check("docs/" in (i.get("basis_doc") or ""), f"{i['id']}: не указана заметка проекта")

    for i in items:
        for lang in ("ru", "uz", "en"):
            check(bool(i["q"][lang]), f"{i['id']}: нет вопроса на {lang}")
            check(bool(i["a"][lang]), f"{i['id']}: нет ответа на {lang}")
        # узбекский — латиница с апострофом ʻ
        uz = i["q"]["uz"] + " " + i["a"]["uz"]
        check(not re.search(r"[’‘'`]", uz), f"{i['id']}: в узбекском тексте неверный апостроф")
        check(not re.search(r"[А-Яа-яЎўҚқҒғҲҳ]", uz), f"{i['id']}: узбекский текст не латиницей")
        # английский без официального текста — с пометкой
        for c in i["citations"]:
            for lang, q in c["quote"].items():
                if q is None:
                    continue
                check(len(q) <= 300, f"{i['id']}: цитата {c['act']} {lang} длиннее 300 символов")
                check(any(q in t for t in texts.values()),
                      f"{i['id']}: цитата {c['act']} {c['article']} ({lang}) не найдена в библиотеке")
                # в цитату не должны попадать комментарии редакции и заголовки соседних статей
                for junk in ("Комментарий LexUz", "См. предыдущую редакцию", "LexUZ sharhi",
                             "Oldingi tahrirga qarang", "См. Положение о Государственном реестре"):
                    check(junk not in q, f"{i['id']}: в цитате {c['article']} ({lang}) чужой текст «{junk}»")
                check(not re.search(r"Статья \d+\.\s*$|\d+-modda\.\s*$", q),
                      f"{i['id']}: цитата {c['article']} ({lang}) заканчивается заголовком другой статьи")
            check(any((c["quote"] or {}).values()),
                  f"{i['id']}: у цитаты {c['article']} нет текста ни на одном языке")
            check(any((c["url"] or {}).values()),
                  f"{i['id']}: у цитаты {c['article']} нет ссылки на источник")
            for lang, u in (c["url"] or {}).items():
                if not u:
                    continue
                check(u.startswith("https://lex.uz/"), f"{i['id']}: ссылка не на lex.uz: {u}")
                # /uz/docs/-<номер> — узбекская латиница (дефис обязателен, проверено 22.09.2026)
                check(not re.search(r"lex\.uz/[a-z]{2}/docs/-{2,}", u), f"{i['id']}: испорченная ссылка {u}")
            check(c["official"]["uz"] is True and c["official"]["ru"] is False,
                  f"{i['id']}: официальным должен считаться узбекский текст")

    if errors:
        print("ОШИБКИ:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print(f"FAQ в порядке: вопросов {len(items)} (норма {len(norms)}, практика {len(practice)}), "
          f"цитат {sum(len(i['citations']) for i in items)}, все цитаты найдены в текстах актов.")


if __name__ == "__main__":
    main()
