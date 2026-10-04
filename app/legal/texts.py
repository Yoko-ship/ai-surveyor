"""Тексты ответа специалиста на трёх языках: имя и роль помощника, подписи источников и частей ответа,
пометки «нормы нет», «только на другом языке», «акт изменился». Только данные и две подписи."""
from . import market as mx

LANGS = ("ru", "uz", "en")
DEFAULT_LANG = "ru"

# Как зовут помощника в ответах API (мини-апп показывает это имя пользователю)
ASSISTANT_NAME = {
    "ru": "ИИ специалист по страхованию INSON",
    "uz": "INSON sugʻurta boʻyicha sunʼiy intellekt mutaxassisi",
    "en": "INSON AI insurance specialist",
}
# роль помощника (поле assistant_role ответа)
ASSISTANT_ROLE = dict(mx.ROLE)

# тип источника куска индекса и подпись для человека
SOURCE_LABEL = {
    "law": {"ru": "закон", "uz": "qonun", "en": "law"},
    "company": {"ru": "тарифная политика INSON", "uz": "INSON tarif siyosati", "en": "INSON tariff policy"},
    "market": {"ru": "данные НАПП", "uz": "NAPP maʼlumotlari", "en": "NAPP data"},
    "competitor": {"ru": "документ другого страховщика — не норма", "uz": "boshqa sugʻurtalovchi hujjati — norma emas",
                   "en": "another insurer's document — not a legal rule"},
    "note": {"ru": "заметка проекта", "uz": "loyiha qaydi", "en": "project note"},
    "ai": {"ru": "ответ ИИ — не подтверждён источником", "uz": "SI javobi — manba bilan tasdiqlanmagan",
           "en": "AI answer — not confirmed by a source"},
    "none": {"ru": "источник не найден", "uz": "manba topilmadi", "en": "no source found"},
}
# пометки частей ответа: данные (с источником) и мнение/вывод
PART_LABEL = {
    "data": {"ru": "Данные (с источником)", "uz": "Maʼlumotlar (manba bilan)", "en": "Data (with source)"},
    "opinion": {"ru": "Вывод — мнение, а не норма и не данные", "uz": "Xulosa — fikr, norma yoki maʼlumot emas",
                "en": "Conclusion — an opinion, not a rule or data"},
}


def source_label(kind: str, lang: str) -> str:
    d = SOURCE_LABEL.get(kind) or SOURCE_LABEL["none"]
    return d.get(lang) or d[DEFAULT_LANG]


# пометка практического ответа: норма его не покрывает
PRACTICE_NOTE = {
    "ru": "Ответ по практике компании и учебникам CII, а не по норме права.",
    "uz": "Javob huquqiy norma emas, kompaniya amaliyoti va CII darsliklari asosida berildi.",
    "en": "This answer follows company practice and CII textbooks, not a legal rule.",
}
# пометка ответа, собранного моделью
AI_NOTE = {
    "ru": "ИИ: ответ подготовлен моделью, норма его не подтверждает — проверьте у юриста.",
    "uz": "SI: javobni model tayyorladi, norma bilan tasdiqlanmagan — yuristda tekshiring.",
    "en": "AI: drafted by the model and not confirmed by a rule — check with the lawyer.",
}

NO_NORM = {
    "ru": "В законодательстве прямой нормы не найдено — смотрите правила страхования компании "
          "и договор; ниже ближайшие по смыслу статьи",
    "uz": "Qonunchilikda toʻgʻridan-toʻgʻri norma topilmadi — kompaniyaning sugʻurta qoidalari "
          "va shartnomaga qarang; quyida maʼno jihatdan eng yaqin moddalar",
    "en": "No direct provision found in the legislation — see the company's insurance rules and "
          "the contract; closest articles below",
}
# тот же ответ, когда ближайших статей показать нечего (вопрос вообще не о страховом праве)
NO_NORM_BARE = {
    "ru": "В законодательстве прямой нормы не найдено — смотрите правила страхования компании "
          "и договор.",
    "uz": "Qonunchilikda toʻgʻridan-toʻgʻri norma topilmadi — kompaniyaning sugʻurta qoidalari "
          "va shartnomaga qarang.",
    "en": "No direct provision found in the legislation — see the company's insurance rules and "
          "the contract.",
}
NO_NORM_NOTE = {
    "ru": "нормы по этому вопросу в базе не найдено — смотрите правила страхования",
    "uz": "bu savol boʻyicha bazada norma topilmadi — sugʻurta qoidalariga qarang",
    "en": "no provision found in the database — see the insurance rules",
}

# норма существует только на другом языке (Положение 3845 — только узбекский текст)
NOTE_ONLY_LANG = {
    "ru": "текст нормы есть только на языке: %s — показан оригинал и ссылка на него",
    "uz": "normaning matni faqat %s tilida mavjud — asl matn va unga havola koʻrsatilgan",
    "en": "the provision exists only in %s — the original text and its link are shown",
}

SILENCE_NOTE = {
    "ru": "законом не установлено — регулируется правилами страхования и договором",
    "uz": "qonun bilan belgilanmagan — sugʻurta qoidalari va shartnoma bilan tartibga solinadi",
    "en": "not set by law — governed by the insurance rules and the contract",
}

NOTE_NO_LANG = {
    "uz":"Bu hujjatning oʻzbekcha matni bazada yoʻq — javob rus tilidagi matn asosida.",
    "en": "The Uzbek/English text of this act is not in the database — the answer is based on the Russian text.",
    "ru": "узбекский/английский текст этого акта в базе отсутствует",
}

ACTUALITY_TEXT = {
    "ru": "Акт изменился на lex.uz%s — цитата из базы может быть из прежней редакции. "
          "Сверьте с действующей редакцией: %s",
    "uz": "Hujjat lex.uz saytida oʻzgargan%s — bazadagi iqtibos avvalgi tahrirdan boʻlishi mumkin. "
          "Amaldagi tahrir bilan solishtiring: %s",
    "en": "The act has changed on lex.uz%s — the quote from the database may be from the previous "
          "version. Check the current version: %s",
}
ACTUALITY_SINCE = {"ru": " (редакция от %s)", "uz": " (%s tahriri)", "en": " (version of %s)"}
ACTUALITY_NOTE = {
    "ru": "есть цитаты из актов, изменившихся на lex.uz: правила по ним требуют пересмотра юристом",
    "uz": "lex.uz da oʻzgargan hujjatlardan iqtiboslar bor: ular boʻyicha qoidalar yurist tomonidan "
          "qayta koʻrib chiqilishi kerak",
    "en": "some quotes come from acts that have changed on lex.uz: the related rules need the "
          "lawyer's review",
}

COMPETITOR_TEXT = {
    "ru": ("По документам других страховщиков (не норма — их правила, оферты и страницы продуктов):",
           "В документах других страховщиков в базе ответа не нашлось."),
    "uz": ("Boshqa sugʻurtalovchilar hujjatlari boʻyicha (norma emas — ularning qoidalari va ofertalari):",
           "Bazadagi boshqa sugʻurtalovchilar hujjatlarida javob topilmadi."),
    "en": ("From other insurers' documents (not a legal rule — their rules, offers and product pages):",
           "Nothing found in other insurers' documents in the database."),
}


def part_label(part: str, lang: str) -> str:
    """Подпись части ответа: data — данные с источником, opinion — вывод."""
    return PART_LABEL[part].get(lang) or PART_LABEL[part][DEFAULT_LANG]
