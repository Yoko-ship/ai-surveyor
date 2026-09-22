# -*- coding: utf-8 -*-
"""
Практическая часть FAQ «ИИ специалист по страхованию»: вопросы, ответ на которые даёт не норма,
а практика компании и учебники (CII M05/M97, заметки проекта в docs/*.md).

Почему отдельный файл: у этих ответов нет и не может быть цитаты из lex.uz. Вместо цитаты у них
поле basis — «заметка проекта: <файл> · практика компании / учебник CII». Собирается вместе с
нормативной частью в tools/faq_build.py.

Узбекский текст — латиница, апостроф только ʻ (проверяется tests/test_faq.py).
"""

# Заметки проекта, на которые опираются ответы (все файлы лежат в docs/)
D_PML = "docs/Максимальный убыток и собственное удержание.md"
D_CAP = "docs/Резервы, ёмкость и собственное удержание.md"
D_CII = "docs/Фундаментальные знания из учебников CII.md"
D_FRA = "docs/Франшиза — правовые рамки и правовой блок промпта.md"
D_VAL = "docs/Оценка стоимости объекта.md"
D_DOC = "docs/Чек-листы документов.md"
D_SPEC = "docs/Спецтехника и транспорт — граница.md"
D_OSGOR = "docs/ОСГОР — оценка риска.md"
D_STAT = "docs/Статистика по рискам классов.md"
D_TERM = "docs/Терминология — ru-uz-en.md"
D_OSAGO = "docs/ОСАГО — убыточность и рычаги.md"

LABEL = {
    "ru": "заметка проекта: %s · практика компании / учебник CII",
    "uz": "loyiha eslatmasi: %s · kompaniya amaliyoti / CII darsligi",
    "en": "project note: %s · company practice / CII textbook",
}


def P(id, tags, q, a, doc):
    """Практический вопрос: без нормы, с пометкой об источнике на трёх языках."""
    return {"id": id, "tags": list(tags),
            "q": {"ru": q[0], "uz": q[1], "en": q[2]},
            "a": {"ru": a[0], "uz": a[1], "en": a[2]},
            "citations": [], "rule_codes": [],
            "kind": "практика",
            "basis_doc": doc,
            "basis": {lang: LABEL[lang] % doc for lang in ("ru", "uz", "en")}}


def items():
    it = []
    A = it.append

    # ---------------------------------------------------------------- андеррайтинг: убытки
    A(P("pml_eml_mfl", ["pml", "eml", "mfl", "максимальный убыток", "eng katta zarar", "maximum loss"],
        ("Что такое PML, EML и MFL простыми словами?",
         "PML, EML va MFL oddiy soʻz bilan nima?",
         "What are PML, EML and MFL in plain words?"),
        ("EML — убыток, которого разумно ждать: горит один противопожарный отсек, защита работает. "
         "PML — убыток, когда защита не сработала, но здание устояло. MFL — худший мыслимый случай: "
         "объект гибнет целиком. Ставку и удержание считаем по EML и PML, перестрахование — по MFL.",
         "EML — oqilona kutiladigan zarar: bitta yongʻin boʻlimi yonadi, himoya ishlaydi. "
         "PML — himoya ishlamagan, lekin bino saqlanib qolgan holdagi zarar. MFL — eng yomon holat: "
         "obyekt butunlay yoʻq boʻladi. Stavka va oʻz ushlab qolish EML va PML boʻyicha, qayta "
         "sugʻurta MFL boʻyicha hisoblanadi.",
         "EML is the loss reasonably expected: one fire compartment burns and protection works. "
         "PML is the loss when protection fails but the building survives. MFL is the worst case: "
         "the object is destroyed. Rate and retention follow EML and PML, reinsurance follows MFL."),
        D_PML))

    A(P("eml_how", ["как считается eml", "отсек", "доля гибели", "eml qanday", "how eml is calculated"],
        ("Как система считает EML по зданию?",
         "Tizim bino boʻyicha EML ni qanday hisoblaydi?",
         "How does the system calculate EML for a building?"),
        ("Берётся самый большой противопожарный отсек и умножается на долю гибели. Долю задают "
         "защита (сигнализация, спринклеры, охрана), конструкция, горючесть содержимого и удалённость "
         "пожарной части. Это экспертная оценка, она помечена как некалиброванная.",
         "Eng katta yongʻin boʻlimi olinadi va yoʻqolish ulushiga koʻpaytiriladi. Ulushni himoya "
         "(signalizatsiya, sprinklerlar, qorovul), konstruksiya, ichidagi mol-mulkning yonuvchanligi va "
         "yongʻin boʻlimining uzoqligi belgilaydi. Bu ekspert bahosi, u kalibrlanmagan deb belgilangan.",
         "The largest fire compartment is taken and multiplied by a damage share. The share depends on "
         "protection (alarm, sprinklers, guard), construction, combustibility of contents and distance to "
         "the fire brigade. This is an expert estimate, flagged as not calibrated."),
        D_PML))

    A(P("mfl_why", ["зачем mfl", "перестрахование", "худший случай", "nega mfl", "why mfl"],
        ("Зачем считать MFL, если он почти не случается?",
         "MFL deyarli yuz bermasa, uni nega hisoblaymiz?",
         "Why calculate MFL if it almost never happens?"),
        ("MFL показывает, сколько компания теряет в худшем дне. По нему решают, хватает ли собственного "
         "удержания и нужно ли перестрахование. Частота тут не важна: важно, переживёт ли компания событие.",
         "MFL kompaniya eng yomon kunda qancha yoʻqotishini koʻrsatadi. Shunga qarab oʻz ushlab qolish "
         "yetarlimi va qayta sugʻurta kerakmi degan qaror qabul qilinadi. Bu yerda chastota muhim emas: "
         "kompaniya hodisadan omon qoladimi — shu muhim.",
         "MFL shows how much the company loses on its worst day. It decides whether the retention is "
         "enough and whether reinsurance is needed. Frequency does not matter here: survival does."),
        D_PML))

    # ---------------------------------------------------------------- франшиза
    A(P("franchise_what", ["франшиза", "что такое франшиза", "franshiza", "deductible"],
        ("Что такое франшиза простыми словами?",
         "Franshiza oddiy soʻz bilan nima?",
         "What is a deductible in plain words?"),
        ("Франшиза — часть убытка, которую клиент оставляет себе. Обычно её ставят в процентах от "
         "страховой суммы или твёрдой суммой. Мелкие убытки клиент оплачивает сам, а премия за это ниже.",
         "Franshiza — zararning mijoz oʻzida qoldiradigan qismi. Odatda u sugʻurta summasining foizida "
         "yoki qatʼiy summada belgilanadi. Mayda zararlarni mijoz oʻzi toʻlaydi, buning evaziga mukofot kamayadi.",
         "A deductible is the part of a loss the client keeps. It is usually set as a percentage of the "
         "sum insured or as a fixed amount. Small losses are paid by the client and the premium is lower."),
        D_FRA))

    A(P("franchise_choose", ["как выбрать франшизу", "подобрать франшизу", "franshizani tanlash",
                             "choose deductible"],
        ("Как выбирают размер франшизы?",
         "Franshiza miqdori qanday tanlanadi?",
         "How is the size of the deductible chosen?"),
        ("Смотрят, какой убыток клиент спокойно оплатит сам, и историю мелких убытков за 3 года. "
         "Франшиза должна отсечь частые мелкие убытки, но остаться посильной. Условие берут из правил "
         "страхования компании: своих вариантов мы не придумываем.",
         "Mijoz oʻzi bemalol toʻlay oladigan zarar miqdori va 3 yillik mayda zararlar tarixi koʻriladi. "
         "Franshiza tez-tez uchraydigan mayda zararlarni kesishi, lekin mijozga ogʻir boʻlmasligi kerak. "
         "Shart kompaniyaning sugʻurta qoidalaridan olinadi: oʻzimizdan variant oʻylab topmaymiz.",
         "Look at the loss the client can absorb and at the three-year record of small claims. The "
         "deductible should cut off frequent small losses yet stay affordable. The wording is taken from "
         "the company insurance rules; we do not invent options."),
        D_FRA))

    A(P("franchise_effect", ["франшиза и премия", "скидка за франшизу", "franshiza mukofot",
                             "deductible premium"],
        ("Насколько франшиза снижает премию?",
         "Franshiza mukofotni qanchaga kamaytiradi?",
         "How much does a deductible reduce the premium?"),
        ("В расчёте франшиза — обычный коэффициент: система показывает новую премию и экономию в сумах "
         "прямо в блоке рекомендаций. Точная величина зависит от класса и набора рисков, поэтому "
         "смотрите цифру расчёта, а не общее правило.",
         "Hisob-kitobda franshiza oddiy koeffitsiyent: tizim yangi mukofot va soʻmdagi tejamni tavsiyalar "
         "blokida koʻrsatadi. Aniq miqdor sinf va risklar toʻplamiga bogʻliq, shuning uchun umumiy qoidaga "
         "emas, hisob raqamiga qarang.",
         "In the calculation the deductible is an ordinary factor: the system shows the new premium and "
         "the saving in soum in the recommendations block. The exact effect depends on the class and the "
         "perils, so rely on the calculated figure, not on a rule of thumb."),
        D_FRA))

    # ---------------------------------------------------------------- ставка
    A(P("rate_lower", ["что снижает ставку", "скидка", "stavkani kamaytiradi", "reduce the rate"],
        ("Что снижает ставку по имуществу?",
         "Mol-mulk boʻyicha stavkani nima kamaytiradi?",
         "What reduces the property rate?"),
        ("Франшиза, охрана и сигнализация, спринклеры, негорючая конструкция, отсутствие убытков за 3 года, "
         "исключение отдельных рисков и приведение страховой суммы к стоимости. Все эти рычаги система "
         "перебирает сама и показывает экономию в сумах.",
         "Franshiza, qorovul va signalizatsiya, sprinklerlar, yonmaydigan konstruksiya, 3 yil ichida zarar "
         "boʻlmagani, ayrim risklarni chiqarib tashlash va sugʻurta summasini qiymatga tenglashtirish. "
         "Tizim bu richaglarni oʻzi saralab, soʻmdagi tejamni koʻrsatadi.",
         "A deductible, guard and alarm, sprinklers, non-combustible construction, a clean three-year loss "
         "record, excluding selected perils and aligning the sum insured with the value. The system tries "
         "these levers itself and shows the saving in soum."),
        D_CII))

    A(P("rate_higher", ["что повышает ставку", "надбавка", "stavkani oshiradi", "increase the rate"],
        ("Что повышает ставку?",
         "Stavkani nima oshiradi?",
         "What increases the rate?"),
        ("Горючая конструкция и содержимое, отсутствие защиты, износ, высокая сейсмозона, повторяющиеся "
         "убытки и включение катастрофических рисков (землетрясение, сель, оползень). Каждый множитель "
         "виден в объяснении расчёта построчно.",
         "Yonuvchan konstruksiya va mol-mulk, himoyaning yoʻqligi, eskirish, yuqori seysmik zona, takrorlanuvchi "
         "zararlar va katastrofik risklarni (zilzila, sel, surilma) qoʻshish. Har bir koʻpaytuvchi hisob "
         "izohida satrma-satr koʻrinadi.",
         "Combustible construction and contents, no protection, wear, a high seismic zone, repeated losses and "
         "the inclusion of catastrophe perils (earthquake, mudflow, landslide). Every multiplier is shown "
         "line by line in the explanation."),
        D_CII))

    A(P("underins_example", ["недострахование пример", "пропорция пример", "kam sugʻurta misol",
                             "underinsurance example"],
        ("Покажите недострахование на примере.",
         "Kam sugʻurtalashni misolda koʻrsating.",
         "Show underinsurance with an example."),
        ("Склад стоит 1 млрд, застрахован на 600 млн — это 60% стоимости. Сгорело товара на 100 млн, "
         "выплата составит 60 млн. Это не отказ, а пропорциональное возмещение; предупредить клиента "
         "нужно до подписания.",
         "Ombor 1 mlrd turadi, 600 mln ga sugʻurtalangan — bu qiymatning 60 foizi. 100 mln lik mol yonib "
         "ketsa, toʻlov 60 mln boʻladi. Bu rad etish emas, mutanosib qoplash; mijozni shartnoma imzolangunga "
         "qadar ogohlantirish kerak.",
         "A warehouse worth 1 bn is insured for 600 m, i.e. 60% of value. Goods worth 100 m burn and the "
         "payment is 60 m. This is not a refusal but proportional indemnity; the client must be warned "
         "before signing."),
        D_CII))

    A(P("premium_formula", ["как считается премия", "формула премии", "mukofot qanday hisoblanadi",
                            "premium formula"],
        ("Как считается премия?",
         "Mukofot qanday hisoblanadi?",
         "How is the premium calculated?"),
        ("Премия = применённая ставка × страховая сумма × число дней / 365. Применённая ставка — "
         "наибольшее из технической ставки расчёта и минимального тарифа по продукту. У продукта из "
         "нескольких классов премии частей складываются.",
         "Mukofot = qoʻllanilgan stavka × sugʻurta summasi × kunlar soni / 365. Qoʻllanilgan stavka — "
         "hisobning texnik stavkasi va mahsulot boʻyicha eng kichik tarifdan kattasi. Bir necha sinfdan "
         "iborat mahsulotda qismlarning mukofoti qoʻshiladi.",
         "Premium = applied rate × sum insured × days / 365. The applied rate is the greater of the "
         "technical rate and the product minimum tariff. For a product covering several classes the "
         "part premiums are added up."),
        D_CII))

    A(P("net_gross", ["нетто", "брутто", "нагрузка", "netto brutto", "net gross rate"],
        ("Чем нетто-ставка отличается от брутто?",
         "Netto stavka bruttodan nimasi bilan farq qiladi?",
         "How does the net rate differ from the gross rate?"),
        ("Нетто-ставка покрывает только ожидаемые выплаты. Брутто (техническая) — это нетто плюс рисковая "
         "и катастрофическая надбавки, делённые на единицу минус доля нагрузки. Нагрузка — расходы на "
         "ведение дела и вознаграждение посредника.",
         "Netto stavka faqat kutilayotgan toʻlovlarni qoplaydi. Brutto (texnik) — netto ustiga risk va "
         "katastrofa ustamalari qoʻshilib, bir minus yuklama ulushiga boʻlinadi. Yuklama — ish yuritish "
         "xarajatlari va vositachi haqi.",
         "The net rate covers expected claims only. The gross (technical) rate is the net rate plus risk "
         "and catastrophe loadings, divided by one minus the expense share. The loading covers running "
         "costs and intermediary remuneration."),
        D_CII))

    A(P("loading_parts", ["из чего нагрузка", "расходы на ведение дела", "yuklama", "expense loading"],
        ("Из чего складывается нагрузка в ставке?",
         "Stavkadagi yuklama nimalardan tashkil topadi?",
         "What makes up the loading in the rate?"),
        ("Из расходов на ведение дела, комиссии посредника и прибыли. Доля нагрузки берётся из справочника "
         "компании и видна в объяснении расчёта. Предупредительные мероприятия (РПМ) компания не ведёт, "
         "в нагрузку они не входят.",
         "Ish yuritish xarajatlari, vositachi komissiyasi va foyda. Yuklama ulushi kompaniya maʼlumotnomasidan "
         "olinadi va hisob izohida koʻrinadi. Ogohlantiruvchi tadbirlar (RPM) kompaniyada yuritilmaydi va "
         "yuklamaga kirmaydi.",
         "Running costs, intermediary commission and profit. The expense share comes from the company "
         "reference table and is shown in the explanation. Loss prevention funds are not maintained by the "
         "company and are not part of the loading."),
        D_CII))

    A(P("risk_load", ["рисковая надбавка", "колебания убытков", "risk ustamasi", "risk loading"],
        ("Зачем нужна рисковая надбавка?",
         "Risk ustamasi nima uchun kerak?",
         "Why is the risk loading needed?"),
        ("Фактические убытки колеблются вокруг среднего. Надбавка (у нас 12% нетто до калибровки) даёт "
         "запас, чтобы года с плохой статистикой не съедали капитал. Она экспертная и помечена как "
         "некалиброванная — уточним по выгрузке убытков компании.",
         "Haqiqiy zararlar oʻrtacha atrofida tebranadi. Ustama (kalibrlashgacha netto 12 foizi) yomon "
         "statistikali yillar kapitalni yeb qoʻymasligi uchun zaxira beradi. U ekspert bahosi va "
         "kalibrlanmagan deb belgilangan — kompaniya zararlari yuklamasi boʻyicha aniqlanadi.",
         "Actual losses fluctuate around the mean. The loading (12% of the net rate before calibration) "
         "gives a buffer so that bad years do not eat capital. It is an expert figure marked as not "
         "calibrated and will be refined on the company loss data."),
        D_CII))

    A(P("min_tariff_company", ["минимальный тариф", "54-п", "eng kichik tarif", "minimum tariff"],
        ("Что такое минимальный тариф компании?",
         "Kompaniyaning eng kichik tarifi nima?",
         "What is the company minimum tariff?"),
        ("Это нижняя граница ставки из действующей тарифной политики (графа «минимальный тариф»). "
         "Расчёт не даёт ставку ниже неё; исключения — продукты «по согласованию с ЦО», «по программе» "
         "и «по генеральному договору», где ставка согласуется отдельно.",
         "Bu amaldagi tarif siyosatidagi stavkaning quyi chegarasi («eng kichik tarif» ustuni). Hisob undan "
         "past stavka bermaydi; istisno — «markaz bilan kelishuv boʻyicha», «dastur boʻyicha» va «bosh "
         "shartnoma boʻyicha» mahsulotlar, ularda stavka alohida kelishiladi.",
         "It is the floor rate in the current tariff policy (column «minimum tariff»). The calculation never "
         "goes below it; the exceptions are products priced by head-office agreement, by programme or under "
         "a master agreement, where the rate is agreed separately."),
        D_CII))

    A(P("statutory_tariff", ["обязательные виды", "тариф пкм", "majburiy turlar tarifi",
                             "compulsory tariff"],
        ("Кто устанавливает тариф по обязательным видам?",
         "Majburiy turlar boʻyicha tarifni kim belgilaydi?",
         "Who sets the tariff for compulsory lines?"),
        ("Тариф по обязательным видам задан нормативным актом (постановление Кабинета Министров), а не "
         "расчётом компании. Система показывает ориентир и прямо пишет, что ставку надо взять из акта.",
         "Majburiy turlar boʻyicha tarif kompaniya hisobi bilan emas, meyoriy hujjat (Vazirlar Mahkamasi "
         "qarori) bilan belgilanadi. Tizim faqat moʻljal koʻrsatadi va stavkani hujjatdan olish kerakligini "
         "ochiq yozadi.",
         "The tariff for compulsory lines is set by a regulation (Cabinet of Ministers resolution), not by "
         "the company. The system shows a reference figure and states plainly that the rate must be taken "
         "from the act."),
        D_CII))

    # ---------------------------------------------------------------- ёмкость и перестрахование
    A(P("one_risk_limit", ["лимит на один риск", "20 процентов", "bitta riskka limit", "single risk limit"],
        ("Что такое лимит на один риск и как он считается?",
         "Bitta riskka limit nima va u qanday hisoblanadi?",
         "What is the single risk limit and how is it calculated?"),
        ("Это предел ответственности по одному договору: 20% от суммы собственных средств и резервов на "
         "последнюю отчётную дату. Превышение не запрещает сделку, но требует перестрахования, и система "
         "ставит по этой проверке «стоп».",
         "Bu bitta shartnoma boʻyicha javobgarlik chegarasi: oxirgi hisobot sanasidagi oʻz mablagʻlari va "
         "zaxiralar summasining 20 foizi. Oshib ketish bitimni taqiqlamaydi, lekin qayta sugʻurta talab "
         "qiladi va tizim bu tekshiruvga «stop» qoʻyadi.",
         "It is the cap of liability under one contract: 20% of own funds plus reserves at the last "
         "reporting date. Exceeding it does not forbid the deal but requires reinsurance, and the system "
         "marks that check as a stop."),
        D_CAP))

    A(P("retention", ["собственное удержание", "удержание", "oʻz ushlab qolish", "retention"],
        ("Что такое собственное удержание?",
         "Oʻz ushlab qolish nima?",
         "What is the retention?"),
        ("Это часть риска, которую компания оставляет себе, а остальное передаёт в перестрахование. "
         "Берётся наименьшее из: сценария удержания по объекту, лимита на один риск и таблицы линий класса.",
         "Bu kompaniya oʻzida qoldiradigan risk qismi, qolganini qayta sugʻurtaga beradi. Obyekt boʻyicha "
         "ushlab qolish ssenariysi, bitta riskka limit va sinf liniyalari jadvalidan eng kichigi olinadi.",
         "It is the share of the risk the company keeps, the rest being ceded to reinsurers. It is the "
         "lowest of: the retention scenario for the object, the single risk limit and the class line table."),
        D_CAP))

    A(P("reinsurance_short", ["перестрахование", "в двух словах", "qayta sugʻurta", "reinsurance"],
        ("Перестрахование в двух словах — это что?",
         "Qayta sugʻurta — qisqacha nima?",
         "Reinsurance in two words — what is it?"),
        ("Страховщик страхует сам себя: часть принятого риска он передаёт другому страховщику вместе с "
         "частью премии. Перед клиентом всё равно отвечает наша компания целиком. Расчёты по "
         "перестрахованию в продукт пока не входят.",
         "Sugʻurtalovchi oʻzini sugʻurtalaydi: qabul qilgan riskning bir qismini mukofotning bir qismi bilan "
         "birga boshqa sugʻurtalovchiga beradi. Mijoz oldida baribir bizning kompaniya toʻliq javob beradi. "
         "Qayta sugʻurta hisob-kitoblari hozircha mahsulotga kirmaydi.",
         "The insurer insures itself: part of the accepted risk is passed to another insurer together with "
         "part of the premium. Towards the client our company remains fully liable. Reinsurance "
         "calculations are not part of the product yet."),
        D_CAP))

    A(P("fac_treaty", ["факультативное", "облигаторное", "fakultativ", "facultative treaty"],
        ("Чем факультативное перестрахование отличается от облигаторного?",
         "Fakultativ qayta sugʻurta obligatordan nimasi bilan farq qiladi?",
         "How does facultative reinsurance differ from treaty?"),
        ("Факультативное — по каждому риску отдельно, обе стороны свободны в решении. Облигаторное — "
         "заранее заключённый договор, по которому все подходящие риски передаются автоматически. Крупные "
         "единичные объекты обычно идут факультативно.",
         "Fakultativ — har bir risk boʻyicha alohida, ikkala tomon ham erkin qaror qiladi. Obligator — oldindan "
         "tuzilgan shartnoma, unga mos keladigan barcha risklar avtomatik oʻtadi. Yirik yakka obyektlar "
         "odatda fakultativ tarzda beriladi.",
         "Facultative is risk by risk, both sides free to decide. Treaty is a pre-agreed contract under "
         "which all qualifying risks cede automatically. Large single objects usually go facultative."),
        D_CAP))

    A(P("coinsurance", ["сострахование", "несколько страховщиков", "hamkorlikda sugʻurta", "coinsurance"],
        ("Чем сострахование отличается от перестрахования?",
         "Hamkorlikda sugʻurta qayta sugʻurtadan nimasi bilan farq qiladi?",
         "How does coinsurance differ from reinsurance?"),
        ("При состраховании несколько страховщиков подписывают один договор с клиентом, каждый отвечает "
         "своей долей. При перестраховании договор с клиентом один и отвечает по нему только наша компания.",
         "Hamkorlikda sugʻurtada bir necha sugʻurtalovchi mijoz bilan bitta shartnomani imzolaydi, har biri "
         "oʻz ulushi boʻyicha javob beradi. Qayta sugʻurtada mijoz bilan shartnoma bitta va u boʻyicha faqat "
         "bizning kompaniya javob beradi.",
         "In coinsurance several insurers sign one contract with the client, each liable for its share. In "
         "reinsurance there is one contract with the client and only our company is liable under it."),
        D_CAP))

    # ---------------------------------------------------------------- обязательные виды
    A(P("osgor_short", ["осгор", "ответственность работодателя", "ish beruvchi javobgarligi",
                        "employer liability"],
        ("Что такое ОСГОР коротко?",
         "OSGOR qisqacha nima?",
         "What is compulsory employer liability insurance in brief?"),
        ("Обязательное страхование гражданской ответственности работодателя за вред жизни и здоровью "
         "работника при исполнении трудовых обязанностей. Страховая сумма — годовой фонд оплаты труда, "
         "тариф и коэффициент по виду деятельности заданы актом.",
         "Ish beruvchining xodim mehnat vazifalarini bajarish paytida hayoti va sogʻligʻiga yetkazilgan zarar "
         "uchun fuqarolik javobgarligini majburiy sugʻurtalash. Sugʻurta summasi — yillik mehnatga haq toʻlash "
         "fondi, tarif va faoliyat turi koeffitsiyenti hujjat bilan belgilangan.",
         "Compulsory insurance of the employer liability for harm to the life and health of an employee at "
         "work. The sum insured is the annual payroll; the tariff and the activity coefficient are set by "
         "the regulation."),
        D_OSGOR))

    A(P("osgor_premium", ["премия осгор", "фот", "кст", "osgor mukofoti", "employer liability premium"],
        ("Как считается премия по ОСГОР?",
         "OSGOR boʻyicha mukofot qanday hisoblanadi?",
         "How is the employer liability premium calculated?"),
        ("Годовой фонд оплаты труда × базовый тариф × коэффициент вида деятельности по ОКЭД. Есть нижняя "
         "граница премии в долях БРВ: если расчёт меньше, берётся минимум. Код ОКЭД надо сверить с "
         "заявлением — от него зависит коэффициент.",
         "Yillik mehnatga haq toʻlash fondi × asosiy tarif × IFUT boʻyicha faoliyat turi koeffitsiyenti. "
         "Mukofotning BHM ulushidagi quyi chegarasi bor: hisob undan kam boʻlsa, eng kam miqdor olinadi. "
         "IFUT kodini arizaga solishtirish kerak — koeffitsiyent shunga bogʻliq.",
         "Annual payroll × base tariff × activity coefficient by the national activity classifier. There is "
         "a floor premium expressed in base calculation units: if the result is lower, the floor applies. "
         "Check the activity code, the coefficient depends on it."),
        D_OSGOR))

    A(P("osgo_short", ["осго", "владельцы транспортных средств", "avtoegalari javobgarligi",
                       "motor third party"],
        ("Что такое ОСГО владельцев транспортных средств коротко?",
         "Transport vositalari egalarining majburiy javobgarlik sugʻurtasi qisqacha nima?",
         "What is compulsory motor third party liability in brief?"),
        ("Обязательное страхование ответственности водителя за вред третьим лицам: жизнь, здоровье и "
         "имущество потерпевшего. Своё авто оно не покрывает — для этого отдельный договор каско по классу 3.",
         "Haydovchining uchinchi shaxslarga — jabrlanuvchining hayoti, sogʻligʻi va mol-mulkiga yetkazilgan "
         "zarar uchun javobgarligini majburiy sugʻurtalash. Oʻz avtomobilini qoplamaydi — buning uchun 3-sinf "
         "boʻyicha alohida kasko shartnomasi kerak.",
         "Compulsory insurance of the driver liability to third parties: life, health and property of the "
         "victim. It does not cover the own vehicle — that needs a separate own-damage contract in class 3."),
        D_OSAGO))

    A(P("osgop_short", ["осгоп", "перевозчик", "tashuvchi javobgarligi", "carrier liability"],
        ("Что такое ОСГОП перевозчика коротко?",
         "Tashuvchining majburiy javobgarlik sugʻurtasi qisqacha nima?",
         "What is compulsory carrier liability in brief?"),
        ("Обязательное страхование ответственности перевозчика перед пассажирами за вред жизни, здоровью "
         "и багажу во время перевозки. Обязанность и условия заданы отдельным законом, тариф — актом.",
         "Tashuvchining tashish paytida yoʻlovchilar hayoti, sogʻligʻi va bagajiga yetkazilgan zarar uchun "
         "javobgarligini majburiy sugʻurtalash. Majburiyat va shartlar alohida qonun bilan, tarif hujjat "
         "bilan belgilangan.",
         "Compulsory insurance of the carrier liability to passengers for harm to life, health and luggage "
         "during carriage. The duty and terms are set by a separate law, the tariff by a regulation."),
        D_OSAGO))

    # ---------------------------------------------------------------- документы
    A(P("docs_by_class", ["какие документы", "чек-лист", "qanday hujjatlar", "which documents"],
        ("Какие документы нужны для анализа риска?",
         "Riskni tahlil qilish uchun qanday hujjatlar kerak?",
         "Which documents are needed to analyse the risk?"),
        ("Общие: документ о праве на объект, документ о стоимости, справка об убытках за 3 года, "
         "регистрация страхователя. Дальше — по классу. Заявление-анкета относится к оформлению договора "
         "и в анализ риска не входит (правка заказчика 22.09.2026).",
         "Umumiy: obyektga boʻlgan huquq hujjati, qiymat hujjati, 3 yillik zararlar maʼlumotnomasi, "
         "sugʻurta qildiruvchining roʻyxatdan oʻtgani. Keyingilari sinf boʻyicha. Ariza-anketa shartnoma "
         "rasmiylashtirishga tegishli va risk tahliliga kirmaydi (buyurtmachi tuzatishi 22.09.2026).",
         "General: title document, value document, three-year loss record, registration of the "
         "policyholder. The rest depends on the class. The proposal form belongs to contract issuance and "
         "is not part of the risk analysis (client decision of 22.09.2026)."),
        D_DOC))

    A(P("docs_class8", ["документы класс 8", "имущество", "8-sinf hujjatlari", "class 8 documents"],
        ("Какие документы нужны по имуществу (класс 8)?",
         "Mol-mulk boʻyicha (8-sinf) qanday hujjatlar kerak?",
         "Which documents are needed for property (class 8)?"),
        ("Отчёт оценщика или справка по основным средствам, технический паспорт или кадастровые документы, "
         "фотографии объекта, сведения о пожарной сигнализации и охране. По условию добавляются план "
         "противопожарных отсеков, заключение пожарной службы, акт проверки электрохозяйства.",
         "Baholovchi hisoboti yoki asosiy vositalar maʼlumotnomasi, texnik pasport yoki kadastr hujjatlari, "
         "obyekt fotosuratlari, yongʻin signalizatsiyasi va qorovul haqida maʼlumot. Shartga koʻra yongʻin "
         "boʻlimlari rejasi, yongʻin xizmati xulosasi, elektr xoʻjaligi tekshiruv dalolatnomasi qoʻshiladi.",
         "Valuer report or fixed assets statement, technical passport or cadastral documents, photographs "
         "of the object, details of fire alarm and guarding. On condition: compartment plan, fire brigade "
         "opinion, electrical inspection report."),
        D_DOC))

    A(P("docs_class9", ["документы класс 9", "товар в обороте", "9-sinf hujjatlari", "class 9 documents"],
        ("Что нужно по товару на складе (класс 9)?",
         "Ombordagi mol boʻyicha (9-sinf) nima kerak?",
         "What is needed for goods in a warehouse (class 9)?"),
        ("Инвентаризационная опись товара, сведения об охране и сигнализации, данные о помещении. Без учёта "
         "остатков размер убытка при краже или пожаре доказать нечем — об этом предупреждаем заранее.",
         "Molning inventarizatsiya roʻyxati, qorovul va signalizatsiya haqida maʼlumot, bino haqidagi "
         "maʼlumotlar. Qoldiqlar hisobisiz oʻgʻirlik yoki yongʻindagi zarar miqdorini isbotlab boʻlmaydi — "
         "bu haqda oldindan ogohlantiramiz.",
         "Stock inventory, details of guarding and alarm, data on the premises. Without stock records the "
         "amount of a theft or fire loss cannot be proved — we warn about this in advance."),
        D_DOC))

    A(P("docs_class3", ["документы транспорт", "класс 3", "transport hujjatlari", "class 3 documents"],
        ("Что нужно по транспорту (класс 3)?",
         "Transport boʻyicha (3-sinf) nima kerak?",
         "What is needed for a vehicle (class 3)?"),
        ("Технический паспорт и свидетельство о регистрации, документ о стоимости (счёт дилера или отчёт "
         "оценщика), фотографии с четырёх сторон и фото одометра, сведения о противоугонной системе и "
         "ночном хранении, список допущенных водителей. В залоге — договор с банком или лизингом.",
         "Texnik pasport va roʻyxatdan oʻtganlik guvohnomasi, qiymat hujjati (diler hisobi yoki baholovchi "
         "hisoboti), toʻrt tomondan fotosuratlar va odometr fotosurati, oʻgʻirlikka qarshi tizim va tunda "
         "saqlash haqida maʼlumot, ruxsat etilgan haydovchilar roʻyxati. Garovda boʻlsa — bank yoki lizing "
         "shartnomasi.",
         "Technical passport and registration certificate, value document (dealer invoice or valuer report), "
         "photographs from four sides and of the odometer, details of the anti-theft device and night "
         "parking, list of admitted drivers. If pledged — the bank or leasing contract."),
        D_DOC))

    A(P("docs_class14", ["документы кредит", "класс 14", "kredit hujjatlari", "class 14 documents"],
        ("Что нужно по кредитному страхованию (класс 14)?",
         "Kredit sugʻurtasi boʻyicha (14-sinf) nima kerak?",
         "What is needed for credit insurance (class 14)?"),
        ("Кредитный договор и график погашения, отчёт об оценке обеспечения, финансовая отчётность "
         "заёмщика, расчёт необеспеченной части кредита. Страхуется только необеспеченная часть и не более "
         "половины суммы кредита, страхователь и плательщик — банк-кредитор.",
         "Kredit shartnomasi va toʻlov jadvali, taʼminotni baholash hisoboti, qarz oluvchining moliyaviy "
         "hisoboti, kreditning taʼminlanmagan qismi hisobi. Faqat taʼminlanmagan qism va kredit summasining "
         "yarmidan koʻp boʻlmagan qismi sugʻurtalanadi, sugʻurta qildiruvchi va toʻlovchi — kredit bergan bank.",
         "Loan agreement and repayment schedule, collateral valuation report, borrower financial statements, "
         "calculation of the uncovered part. Only the uncovered part is insured and no more than half of "
         "the loan; the policyholder and payer is the lending bank."),
        D_DOC))

    # ---------------------------------------------------------------- спецтехника и оценка
    A(P("spec_vs_transport", ["спецтехника", "экскаватор", "граница", "maxsus texnika", "special machinery"],
        ("Экскаватор — это транспорт или спецтехника?",
         "Ekskavator transportmi yoki maxsus texnikami?",
         "Is an excavator a vehicle or special machinery?"),
        ("Класс страхования у них один (3), а документы и коэффициенты разные. Граница по назначению: "
         "машина создана возить людей и грузы по дорогам — транспорт; выполнять работу (копать, поднимать, "
         "бурить) — спецтехника. Смотрим назначение, а не наличие номеров.",
         "Sugʻurta sinfi bitta (3), lekin hujjatlar va koeffitsiyentlar har xil. Chegara vazifaga koʻra: "
         "mashina odam va yukni yoʻlda tashish uchun yaratilgan boʻlsa — transport; ish bajarish uchun "
         "(qazish, koʻtarish, burgʻulash) — maxsus texnika. Raqamga emas, vazifaga qaraymiz.",
         "They share one insurance class (3) but differ in documents and factors. The border is purpose: "
         "built to carry people and cargo on roads — a vehicle; built to do work (dig, lift, drill) — "
         "special machinery. Look at the purpose, not at the plates."),
        D_SPEC))

    A(P("valuation_methods", ["оценка стоимости", "как оценить", "qiymatni baholash", "valuation"],
        ("Как определяют страховую стоимость объекта?",
         "Obyektning sugʻurta qiymati qanday aniqlanadi?",
         "How is the insurable value determined?"),
        ("Три опоры: документы клиента (отчёт оценщика, баланс, счёт дилера), рыночные объявления не старше "
         "6 месяцев со ссылкой на источник и запрос дилеру. Расхождение заявленной и расчётной стоимости "
         "больше 15% — повод запросить подтверждение.",
         "Uchta tayanch: mijoz hujjatlari (baholovchi hisoboti, balans, diler hisobi), 6 oydan oshmagan bozor "
         "eʼlonlari manbaga havola bilan va dilerga soʻrov. Eʼlon qilingan va hisoblangan qiymat farqi "
         "15 foizdan oshsa — tasdiq soʻrash uchun asos.",
         "Three sources: client documents (valuer report, balance sheet, dealer invoice), market "
         "advertisements not older than six months with a link, and a dealer enquiry. A gap of more than "
         "15% between declared and calculated value calls for confirmation."),
        D_VAL))

    A(P("wear_depreciation", ["износ", "амортизация", "eskirish", "depreciation"],
        ("Как учитывается износ?",
         "Eskirish qanday hisobga olinadi?",
         "How is wear taken into account?"),
        ("Износ считается по нормам компании от года выпуска и снижает стоимость, но не ниже остаточного "
         "порога. На ставку износ влияет отдельным коэффициентом: изношенный объект и горит, и ломается чаще.",
         "Eskirish kompaniya meʼyorlari boʻyicha ishlab chiqarilgan yilidan hisoblanadi va qiymatni "
         "kamaytiradi, lekin qoldiq chegaradan past emas. Stavkaga eskirish alohida koeffitsiyent bilan "
         "taʼsir qiladi: eskirgan obyekt koʻproq yonadi va buziladi.",
         "Wear is calculated by the company norms from the year of manufacture and reduces the value, but "
         "not below a residual floor. It also affects the rate through a separate factor: a worn object "
         "burns and breaks more often."),
        D_VAL))

    A(P("value_vs_sum", ["стоимость и страховая сумма", "в чём разница", "qiymat va summa",
                         "value and sum insured"],
        ("Чем страховая стоимость отличается от страховой суммы?",
         "Sugʻurta qiymati sugʻurta summasidan nimasi bilan farq qiladi?",
         "How does the insurable value differ from the sum insured?"),
        ("Стоимость — сколько объект стоит на самом деле. Сумма — на сколько его застраховали и предел "
         "выплаты. Сумма ниже стоимости даёт пропорциональную выплату, выше — превышение ничтожно, премия "
         "за него не возвращается.",
         "Qiymat — obyekt aslida qancha turishi. Summa — u qanchaga sugʻurtalangani va toʻlov chegarasi. "
         "Summa qiymatdan past boʻlsa toʻlov mutanosib, yuqori boʻlsa ortiqcha qism haqiqiy emas va "
         "mukofot qaytarilmaydi.",
         "Value is what the object is really worth. The sum insured is what it is insured for and the cap "
         "of indemnity. A sum below value gives proportional payment; above value the excess is void and "
         "the premium for it is not returned."),
        D_VAL))

    A(P("sum_choice", ["как выбрать страховую сумму", "на сколько страховать", "summani tanlash",
                       "choose sum insured"],
        ("На какую сумму страховать, чтобы не переплатить?",
         "Ortiqcha toʻlamaslik uchun qanday summaga sugʻurtalash kerak?",
         "What sum insured avoids overpaying?"),
        ("По страховой стоимости: ровно она даёт полную выплату без переплаты. Ставить сумму выше смысла "
         "нет — превышение не работает. Ставить ниже можно осознанно, понимая пропорциональную выплату.",
         "Sugʻurta qiymati boʻyicha: aynan u ortiqcha toʻlovsiz toʻliq qoplashni beradi. Summani yuqori "
         "qoʻyishdan maʼno yoʻq — ortiqcha qism ishlamaydi. Pastroq qoʻyish mumkin, lekin mutanosib "
         "toʻlovni tushungan holda.",
         "At the insurable value: it gives full indemnity without overpaying. A higher sum is pointless — "
         "the excess does not work. A lower sum is a conscious choice, accepting proportional payment."),
        D_VAL))

    # ---------------------------------------------------------------- убыток
    A(P("claim_steps", ["что делать при убытке", "страховой случай", "zarar yuz berganda", "what to do at a loss"],
        ("Что клиенту делать при убытке?",
         "Zarar yuz berganda mijoz nima qilishi kerak?",
         "What should the client do when a loss occurs?"),
        ("Сообщить страховщику в срок, указанный в договоре, вызвать компетентные органы (пожарные, милиция, "
         "ГАИ), сохранить место и остатки до осмотра, собрать документы и принять разумные меры к уменьшению "
         "убытка. Решение о выплате принимает страховщик по правилам страхования.",
         "Shartnomada koʻrsatilgan muddatda sugʻurtalovchiga xabar berish, vakolatli organlarni (yongʻin "
         "xizmati, militsiya, YHXX) chaqirish, koʻrikkacha joy va qoldiqlarni saqlash, hujjatlarni yigʻish va "
         "zararni kamaytirish uchun oqilona chora koʻrish. Toʻlov haqidagi qarorni sugʻurtalovchi sugʻurta "
         "qoidalari boʻyicha qabul qiladi.",
         "Notify the insurer within the contract period, call the competent authorities (fire service, "
         "police, traffic police), preserve the site and the remains until inspection, gather documents and "
         "take reasonable steps to reduce the loss. The insurer decides on payment under the rules."),
        D_CII))

    A(P("claim_docs", ["документы по убытку", "какие документы при убытке", "zarar hujjatlari",
                       "claim documents"],
        ("Какие документы собирают по убытку?",
         "Zarar boʻyicha qanday hujjatlar yigʻiladi?",
         "Which documents are collected for a claim?"),
        ("Заявление об убытке, договор (полис), акт компетентного органа о событии, документы о праве на "
         "повреждённое имущество, смету или счета на восстановление, фотографии повреждений. Точный перечень "
         "берут из правил страхования по продукту.",
         "Zarar haqida ariza, shartnoma (polis), hodisa haqida vakolatli organ dalolatnomasi, shikastlangan "
         "mol-mulkka boʻlgan huquq hujjatlari, tiklash smetasi yoki hisoblari, shikast fotosuratlari. Aniq "
         "roʻyxat mahsulot boʻyicha sugʻurta qoidalaridan olinadi.",
         "A loss notification, the contract (policy), the authority report on the event, title documents for "
         "the damaged property, a repair estimate or invoices, photographs of the damage. The exact list "
         "comes from the insurance rules of the product."),
        D_CII))

    A(P("claim_timing", ["срок уведомления", "когда сообщать", "xabar berish muddati", "notification period"],
        ("В какой срок сообщать об убытке?",
         "Zarar haqida qaysi muddatda xabar berish kerak?",
         "Within what period must a loss be reported?"),
        ("Срок задан договором и правилами страхования по продукту — единого срока на все виды нет. "
         "Просрочка не всегда лишает выплаты, но резко усложняет доказывание; сообщать надо сразу, как "
         "стало известно.",
         "Muddat mahsulot boʻyicha shartnoma va sugʻurta qoidalarida belgilanadi — barcha turlar uchun yagona "
         "muddat yoʻq. Kechikish har doim ham toʻlovdan mahrum qilmaydi, lekin isbotlashni keskin "
         "qiyinlashtiradi; maʼlum boʻlishi bilanoq xabar berish kerak.",
         "The period is set by the contract and the product rules — there is no single period for all lines. "
         "A delay does not always forfeit the claim but makes proof much harder; report as soon as you know."),
        D_CII))

    A(P("loss_history", ["убытки за 3 года", "история убытков", "3 yillik zararlar", "loss history"],
        ("Зачем справка об убытках за 3 года?",
         "3 yillik zararlar maʼlumotnomasi nima uchun kerak?",
         "Why is a three-year loss record needed?"),
        ("Прошлые убытки — самый честный показатель риска: повторяющийся убыток означает неустранённую "
         "причину. Справка влияет на коэффициент истории убытков и на условия (франшиза, мероприятия).",
         "Oʻtgan zararlar — riskning eng halol koʻrsatkichi: takrorlanuvchi zarar bartaraf etilmagan sababni "
         "bildiradi. Maʼlumotnoma zararlar tarixi koeffitsiyentiga va shartlarga (franshiza, tadbirlar) "
         "taʼsir qiladi.",
         "Past losses are the most honest measure of risk: a repeated loss means an unfixed cause. The "
         "record drives the loss history factor and the terms (deductible, required measures)."),
        D_STAT))

    # ---------------------------------------------------------------- риск-инженерия
    A(P("fire_compartment", ["противопожарный отсек", "отсек", "yongʻin boʻlimi", "fire compartment"],
        ("Что такое противопожарный отсек и почему он так важен?",
         "Yongʻin boʻlimi nima va u nega muhim?",
         "What is a fire compartment and why does it matter?"),
        ("Это часть здания, отделённая стенами и перекрытиями, через которые огонь не проходит заданное "
         "время. Он ограничивает размер пожара, поэтому EML считается по самому большому отсеку, а не по "
         "всему зданию.",
         "Bu binoning olov belgilangan vaqt davomida oʻta olmaydigan devor va yopmalar bilan ajratilgan "
         "qismi. U yongʻin hajmini cheklaydi, shuning uchun EML butun bino boʻyicha emas, eng katta boʻlim "
         "boʻyicha hisoblanadi.",
         "It is a part of the building separated by walls and floors that fire cannot cross for a set time. "
         "It caps the size of a fire, which is why EML is based on the largest compartment, not the whole "
         "building."),
        D_PML))

    A(P("seismic_zone", ["сейсмозона", "землетрясение", "накопление", "seysmik zona", "seismic zone"],
        ("Почему землетрясение считается отдельно?",
         "Zilzila nega alohida hisoblanadi?",
         "Why is earthquake assessed separately?"),
        ("Землетрясение бьёт по всей площадке сразу: отсеки и сигнализация от него не спасают, поэтому "
         "сценарий считается от стоимости всей площадки с поправками на зону, конструкцию, износ и этажность. "
         "Ещё оно даёт накопление по зоне — много договоров пострадают в один день.",
         "Zilzila butun maydonchani bir vaqtda uradi: boʻlimlar va signalizatsiya undan saqlamaydi, shuning "
         "uchun ssenariy butun maydoncha qiymatidan zona, konstruksiya, eskirish va qavatlar soniga tuzatma "
         "bilan hisoblanadi. Bundan tashqari u zona boʻyicha toʻplanish beradi — bir kunda koʻp shartnoma "
         "zarar koʻradi.",
         "An earthquake hits the whole site at once: compartments and alarms do not help, so the scenario is "
         "based on the value of the entire site adjusted for zone, construction, wear and storeys. It also "
         "creates accumulation by zone — many contracts suffer on the same day."),
        D_PML))

    A(P("preventive_measures", ["предупредительные мероприятия", "предписания", "ogohlantiruvchi tadbirlar",
                                "risk improvement"],
        ("Что такое предписания страхователю?",
         "Sugʻurta qildiruvchiga koʻrsatmalar nima?",
         "What are risk improvement requirements?"),
        ("Это мероприятия, снижающие риск: сигнализация, охрана, разбор причин прошлых убытков, "
         "инвентаризация. Система подбирает их по факторам и рискам объекта, показывает эффект на премию и "
         "помечает, какие при крупной сумме становятся условием договора со сроком исполнения.",
         "Bu riskni kamaytiruvchi tadbirlar: signalizatsiya, qorovul, oʻtgan zararlar sabablarini tahlil "
         "qilish, inventarizatsiya. Tizim ularni obyekt omillari va risklariga qarab tanlaydi, mukofotga "
         "taʼsirini koʻrsatadi va katta summada qaysilari muddatli shartnoma sharti boʻlishini belgilaydi.",
         "These are measures that reduce risk: alarm, guarding, review of past loss causes, stock taking. "
         "The system selects them by the object factors and perils, shows the effect on the premium and "
         "flags which become a contract condition with a deadline when the sum insured is large."),
        D_CII))

    A(P("business_interruption", ["перерыв в производстве", "простой", "ishlab chiqarish toʻxtashi",
                                  "business interruption"],
        ("Что покрывает страхование простоя?",
         "Toʻxtab qolishni sugʻurtalash nimani qoplaydi?",
         "What does business interruption cover?"),
        ("Потерянную прибыль и постоянные расходы за время восстановления после застрахованного убытка. "
         "Для расчёта нужны прибыль и постоянные расходы в месяц и реальный срок восстановления — их "
         "запрашиваем отдельным пунктом чек-листа.",
         "Sugʻurtalangan zarardan keyin tiklash davridagi yoʻqotilgan foyda va doimiy xarajatlar. Hisob uchun "
         "oylik foyda va doimiy xarajatlar hamda haqiqiy tiklash muddati kerak — ularni chek-roʻyxatning "
         "alohida bandi bilan soʻraymiz.",
         "Lost profit and standing charges during the restoration period after an insured loss. The "
         "calculation needs monthly profit and standing charges and a realistic restoration period — we ask "
         "for them as a separate checklist item."),
        D_CII))

    A(P("limit_vs_deductible", ["лимит ответственности", "франшиза и лимит", "javobgarlik limiti",
                                "limit and deductible"],
        ("Чем лимит ответственности отличается от франшизы?",
         "Javobgarlik limiti franshizadan nimasi bilan farq qiladi?",
         "How does a limit of liability differ from a deductible?"),
        ("Франшиза отрезает низ убытка (мелкое платит клиент), лимит — верх (выше предела платит клиент). "
         "Обе величины берутся из правил страхования и обе снижают премию, но защищают от разного.",
         "Franshiza zararning pastini kesadi (maydasini mijoz toʻlaydi), limit — ustini (chegaradan yuqorisini "
         "mijoz toʻlaydi). Ikkalasi ham sugʻurta qoidalaridan olinadi va mukofotni kamaytiradi, lekin har xil "
         "narsadan himoya qiladi.",
         "A deductible cuts the bottom of a loss (the client pays small amounts), a limit cuts the top (the "
         "client pays above the cap). Both come from the insurance rules and both reduce the premium, but "
         "they protect against different things."),
        D_CII))

    A(P("double_insurance_practice", ["двойное страхование", "два полиса", "ikki polis", "double insurance"],
        ("Как заметить двойное страхование на практике?",
         "Amalda ikki karra sugʻurtani qanday sezish mumkin?",
         "How do you spot double insurance in practice?"),
        ("Спросить прямо, есть ли действующие договоры по этому же объекту и рискам, и сверить даты и "
         "страховые суммы в документах клиента. Совпадение объекта, периода и риска — повод передать вопрос "
         "андеррайтеру до выпуска договора.",
         "Shu obyekt va risklar boʻyicha amaldagi shartnomalar bor-yoʻqligini ochiq soʻrash va mijoz "
         "hujjatlaridagi sanalar bilan summalarni solishtirish. Obyekt, davr va risk mos kelsa — shartnoma "
         "chiqarilgunga qadar savolni andarrayterga oʻtkazish kerak.",
         "Ask directly whether other contracts on the same object and perils are in force and compare dates "
         "and sums in the client documents. If object, period and peril coincide, refer the case to the "
         "underwriter before issuing."),
        D_CII))

    A(P("underwriting_what", ["андеррайтинг", "что такое андеррайтинг", "andarrayting", "underwriting"],
        ("Что такое андеррайтинг простыми словами?",
         "Andarrayting oddiy soʻz bilan nima?",
         "What is underwriting in plain words?"),
        ("Это решение, брать ли риск, на каких условиях и по какой цене. Сюрвейер собирает факты и считает, "
         "андеррайтер решает. Система не отказывает от своего имени: спорный риск она помечает «вне "
         "аппетита — на решение андеррайтера».",
         "Bu riskni qabul qilish yoki qilmaslik, qanday shartlarda va qanday narxda degan qaror. Syurveyer "
         "faktlarni yigʻadi va hisoblaydi, andarrayter qaror qiladi. Tizim oʻz nomidan rad etmaydi: bahsli "
         "riskni «ishtahadan tashqari — andarrayter qaroriga» deb belgilaydi.",
         "It is the decision whether to take a risk, on what terms and at what price. The surveyor gathers "
         "facts and calculates, the underwriter decides. The system never declines in its own name: a "
         "doubtful risk is flagged «outside appetite — for the underwriter»."),
        D_CII))

    A(P("tariff_policy", ["тарифная политика", "54-п", "tarif siyosati", "tariff policy"],
        ("Что такое тарифная политика компании?",
         "Kompaniyaning tarif siyosati nima?",
         "What is the company tariff policy?"),
        ("Внутренний документ, где по каждому продукту записаны минимальный тариф и порядок ценообразования: "
         "обычная ставка, «по согласованию с ЦО», «по программе», «по генеральному договору» или тариф по "
         "нормативному акту. Расчёт всегда сверяется с ней.",
         "Ichki hujjat boʻlib, unda har bir mahsulot boʻyicha eng kichik tarif va narx belgilash tartibi "
         "yozilgan: oddiy stavka, «markaz bilan kelishuv boʻyicha», «dastur boʻyicha», «bosh shartnoma "
         "boʻyicha» yoki meyoriy hujjat tarifi. Hisob doimo shunga solishtiriladi.",
         "An internal document listing, for each product, the minimum tariff and the pricing mode: ordinary "
         "rate, head-office agreement, programme, master agreement or a statutory tariff. Every calculation "
         "is checked against it."),
        D_CII))

    A(P("class_meaning", ["класс страхования", "учётная группа", "sugʻurta sinfi", "class of insurance"],
        ("Зачем нужен класс страхования?",
         "Sugʻurta sinfi nima uchun kerak?",
         "Why is the class of insurance needed?"),
        ("Класс определяет базовую ставку, набор факторов, набор рисков и чек-лист документов. Продукт из "
         "нескольких классов считается по каждому классу отдельно, а премии складываются — средняя ставка "
         "по договору ничего не проверяет.",
         "Sinf asosiy stavkani, omillar toʻplamini, risklar toʻplamini va hujjatlar chek-roʻyxatini "
         "belgilaydi. Bir necha sinfdan iborat mahsulot har bir sinf boʻyicha alohida hisoblanadi, mukofotlar "
         "qoʻshiladi — shartnoma boʻyicha oʻrtacha stavka hech nimani tekshirmaydi.",
         "The class sets the base rate, the set of factors, the set of perils and the document checklist. A "
         "product covering several classes is calculated class by class and the premiums are added; an "
         "average contract rate checks nothing."),
        D_TERM))

    A(P("terms_ru_uz_en", ["термины", "перевод терминов", "atamalar", "terminology"],
        ("Где взять правильный перевод страховых терминов?",
         "Sugʻurta atamalarining toʻgʻri tarjimasi qayerdan olinadi?",
         "Where do I get the correct translation of insurance terms?"),
        ("В проекте есть единый словарь терминов ru-uz-en; интерфейс и ответы берут названия оттуда. "
         "Свои варианты перевода не придумываем: расхождение в терминах в договоре потом стоит спора.",
         "Loyihada yagona ru-uz-en atamalar lugʻati bor; interfeys va javoblar nomlarni shu yerdan oladi. "
         "Oʻzimizdan tarjima oʻylab topmaymiz: shartnomadagi atama farqi keyin nizoga olib keladi.",
         "The project keeps a single ru-uz-en glossary; the interface and the answers take the terms from "
         "it. We do not invent our own wording: a mismatch of terms in a contract later costs a dispute."),
        D_TERM))

    A(P("calibrated_flag", ["некалиброванный", "экспертная оценка", "kalibrlanmagan", "not calibrated"],
        ("Что означает пометка «некалиброванный»?",
         "«Kalibrlanmagan» belgisi nimani anglatadi?",
         "What does the «not calibrated» flag mean?"),
        ("Что коэффициент или шкала поставлены экспертно, а не выведены из убытков компании. Такие числа "
         "годятся для сравнения вариантов, но не для утверждения тарифа. Пометка снимется после выгрузки "
         "договоров и убытков за 3–5 лет.",
         "Koeffitsiyent yoki shkala kompaniya zararlaridan emas, ekspert yoʻli bilan qoʻyilganini anglatadi. "
         "Bunday raqamlar variantlarni solishtirishga yaraydi, tarifni tasdiqlashga emas. Belgi 3–5 yillik "
         "shartnoma va zararlar yuklamasidan keyin olinadi.",
         "It means the factor or scale is set by expert judgement, not derived from company losses. Such "
         "figures are fine for comparing options but not for approving a tariff. The flag is removed once "
         "three to five years of contract and loss data arrive."),
        D_STAT))

    A(P("market_rate_compare", ["рынок", "средняя ставка рынка", "bozor stavkasi", "market rate"],
        ("Зачем сравнивать ставку с рынком?",
         "Stavkani bozor bilan solishtirish nima uchun kerak?",
         "Why compare the rate with the market?"),
        ("Средняя ставка и убыточность класса по отчётам регулятора показывают, не выбиваемся ли мы. "
         "Сильное отклонение вниз — риск убыточного портфеля, вверх — риск потерять клиента; и то и другое "
         "решает андеррайтер, а не расчёт.",
         "Regulyator hisobotlaridagi sinf boʻyicha oʻrtacha stavka va zararlilik bizning stavkamiz qanchalik "
         "farq qilishini koʻrsatadi. Kuchli pasayish — zararli portfel xavfi, oshish — mijozni yoʻqotish "
         "xavfi; ikkalasini ham hisob emas, andarrayter hal qiladi.",
         "The class average rate and loss ratio from the regulator reports show whether we are out of line. "
         "A large downward gap risks an unprofitable portfolio, an upward gap risks losing the client; both "
         "are for the underwriter, not for the calculation."),
        D_STAT))

    A(P("takaful_short", ["такафул", "исламское страхование", "takaful", "islamic insurance"],
        ("Что такое такафул коротко?",
         "Takaful qisqacha nima?",
         "What is takaful in brief?"),
        ("Взаимная модель: участники вносят взносы в общий фонд, из него платятся возмещения, а оператор "
         "управляет фондом за вознаграждение. У нас это отдельные продукты, расчёт идёт по тем же классам и "
         "справочникам.",
         "Oʻzaro model: ishtirokchilar umumiy fondga badal kiritadi, undan qoplamalar toʻlanadi, operator esa "
         "fondni haq evaziga boshqaradi. Bizda bu alohida mahsulotlar, hisob oʻsha sinflar va "
         "maʼlumotnomalar boʻyicha boradi.",
         "A mutual model: participants contribute to a common fund, indemnities are paid from it and the "
         "operator manages the fund for a fee. In our system these are separate products calculated on the "
         "same classes and reference tables."),
        D_CII))

    A(P("agent_commission_practice", ["комиссия агента", "вознаграждение", "agent komissiyasi",
                                      "agent commission"],
        ("Как комиссия агента отражается в цене?",
         "Agent komissiyasi narxda qanday aks etadi?",
         "How does agent commission show up in the price?"),
        ("Комиссия входит в нагрузку брутто-ставки, поэтому она уже учтена в премии и не добавляется сверху. "
         "Предельный размер и порядок возврата при досрочном расторжении заданы нормой — смотрите "
         "нормативные вопросы FAQ.",
         "Komissiya brutto stavkaning yuklamasiga kiradi, shuning uchun u mukofotda hisobga olingan va ustiga "
         "qoʻshilmaydi. Eng koʻp miqdor va muddatidan oldin bekor qilinganda qaytarish tartibi norma bilan "
         "belgilangan — FAQ ning meyoriy savollariga qarang.",
         "Commission is part of the loading in the gross rate, so it is already in the premium and is not "
         "added on top. The cap and the refund on early termination are set by regulation — see the legal "
         "questions in the FAQ."),
        D_CII))

    A(P("why_photos", ["фотографии объекта", "зачем фото", "obyekt fotosuratlari", "why photos"],
        ("Зачем нужны фотографии объекта?",
         "Obyekt fotosuratlari nima uchun kerak?",
         "Why are photographs of the object needed?"),
        ("Фото подтверждают состояние, конструкцию и защиту на дату осмотра — это и есть материал сюрвейера. "
         "При убытке они показывают, что было до события, и снимают спор о доснятии повреждений.",
         "Fotosuratlar koʻrik sanasidagi holat, konstruksiya va himoyani tasdiqlaydi — bu syurveyerning "
         "asosiy materiali. Zarar yuz berganda ular hodisadan oldin nima boʻlganini koʻrsatadi va "
         "shikastlanish haqidagi nizoni bartaraf etadi.",
         "Photographs prove the condition, construction and protection at the survey date — that is the "
         "surveyor evidence. At a loss they show what was there before the event and remove arguments about "
         "pre-existing damage."),
        D_DOC))

    A(P("quick_vs_full", ["быстрый расчёт", "полный анализ", "tez hisob", "quick or full"],
        ("Чем быстрый расчёт отличается от полного анализа риска?",
         "Tez hisob toʻliq risk tahlilidan nimasi bilan farq qiladi?",
         "How does a quick quote differ from a full risk analysis?"),
        ("Быстрый расчёт даёт ставку и премию по минимуму данных. Полный анализ добавляет сценарии убытка, "
         "уровень риска, ёмкость, рынок, документы и рекомендации. Для решения андеррайтера нужен полный.",
         "Tez hisob eng kam maʼlumot asosida stavka va mukofot beradi. Toʻliq tahlil zarar ssenariylari, risk "
         "darajasi, sigʻim, bozor, hujjatlar va tavsiyalarni qoʻshadi. Andarrayter qarori uchun toʻliq tahlil kerak.",
         "A quick quote gives the rate and premium from minimal data. The full analysis adds loss scenarios, "
         "the risk level, capacity, market comparison, documents and recommendations. The underwriter "
         "decision needs the full one."),
        D_CII))

    A(P("what_if_no_data", ["не хватает данных", "нет документов", "maʼlumot yetishmaydi", "missing data"],
        ("Что делать, если данных для анализа не хватает?",
         "Tahlil uchun maʼlumot yetishmasa nima qilish kerak?",
         "What if there is not enough data for the analysis?"),
        ("Анализ всё равно считается, но система показывает полноту данных и список «что добавить». Чем "
         "меньше данных, тем осторожнее допущения — все они перечислены в ответе отдельным блоком.",
         "Tahlil baribir hisoblanadi, lekin tizim maʼlumot toʻliqligini va «nima qoʻshish kerak» roʻyxatini "
         "koʻrsatadi. Maʼlumot qancha kam boʻlsa, taxminlar shuncha ehtiyotkor — ularning hammasi javobda "
         "alohida blokda sanab oʻtiladi.",
         "The analysis still runs, but the system shows data completeness and a list of what to add. The "
         "less data, the more conservative the assumptions — all of them are listed in a separate block of "
         "the answer."),
        D_CII))

    return it
