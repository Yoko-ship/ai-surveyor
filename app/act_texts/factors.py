"""Факторы объекта по подгруппам класса, регион и территория страхования.
Часть словаря текстов акта (app/act_texts): склеивается в TX в __init__.py в прежнем порядке."""

# факторы движка (коды справочника коэффициентов) и их значения
FACTOR_LABELS = {
    "construction": {"ru": "Конструкция и огнестойкость", "uz": "Konstruksiya va yongʻinbardoshlik",
                     "en": "Construction and fire resistance"},
    "activity": {"ru": "Деятельность на объекте", "uz": "Obyektdagi faoliyat", "en": "Activity on site"},
    "protection": {"ru": "Защита объекта", "uz": "Obyekt himoyasi", "en": "Protection"},
    "seismic": {"ru": "Сейсмическая зона", "uz": "Seysmik zona", "en": "Seismic zone"},
    "wear": {"ru": "Износ здания", "uz": "Bino eskirishi", "en": "Building wear"},
    "loss_history": {"ru": "Убытки за 3 года", "uz": "3 yillik zararlar", "en": "Losses over 3 years"},
    "franchise": {"ru": "Франшиза", "uz": "Franshiza", "en": "Deductible"},
    "veh_age": {"ru": "Возраст техники", "uz": "Texnika yoshi", "en": "Vehicle age"},
    "veh_type": {"ru": "Тип транспорта", "uz": "Transport turi", "en": "Vehicle type"},
    "antitheft": {"ru": "Противоугонная система", "uz": "Oʻgʻirlikka qarshi tizim", "en": "Anti-theft system"},
    "drivers": {"ru": "Допущенные водители", "uz": "Ruxsat etilgan haydovchilar", "en": "Permitted drivers"},
    "engine_hours": {"ru": "Наработка, моточасов", "uz": "Ishlagan vaqti, motosoat", "en": "Engine hours"},
    "spec_site": {"ru": "Условия эксплуатации", "uz": "Foydalanish sharoiti", "en": "Operating conditions"},
    "spec_operator": {"ru": "Кто управляет машиной", "uz": "Mashinani kim boshqaradi", "en": "Who operates the machine"},
    "spec_guard": {"ru": "Охрана и контроль на площадке", "uz": "Maydonda qoʻriqlash va nazorat",
                   "en": "Site security and control"},
}

OPTION_LABELS = {
    "construction:reinforced": {"ru": "железобетон, кирпич", "uz": "temir-beton, gʻisht",
                                "en": "reinforced concrete, brick"},
    "construction:mixed": {"ru": "смешанные конструкции", "uz": "aralash konstruksiyalar", "en": "mixed construction"},
    "construction:wood": {"ru": "дерево, сэндвич-панели", "uz": "yogʻoch, sendvich-panellar",
                          "en": "timber, sandwich panels"},
    "activity:office": {"ru": "офис, торговля непродовольственная", "uz": "ofis, nooziq-ovqat savdosi",
                        "en": "office, non-food retail"},
    "activity:warehouse": {"ru": "склад общего назначения", "uz": "umumiy ombor", "en": "general warehouse"},
    "activity:food": {"ru": "пищевое производство", "uz": "oziq-ovqat ishlab chiqarish", "en": "food production"},
    "activity:flammable": {"ru": "работа с горючими материалами", "uz": "yonuvchan materiallar bilan ishlash",
                           "en": "work with flammable materials"},
    "protection:none": {"ru": "без сигнализации и охраны", "uz": "signalizatsiya va qoʻriqlashsiz",
                        "en": "no alarm or security"},
    "protection:alarm": {"ru": "пожарная сигнализация", "uz": "yongʻin signalizatsiyasi", "en": "fire alarm"},
    "protection:alarm_guard": {"ru": "сигнализация и охрана", "uz": "signalizatsiya va qoʻriqlash",
                               "en": "alarm and security"},
    "protection:sprinkler": {"ru": "сигнализация, охрана, спринклеры", "uz": "signalizatsiya, qoʻriqlash, sprinklerlar",
                             "en": "alarm, security, sprinklers"},
    "protection:immo": {"ru": "сигнализация и иммобилайзер", "uz": "signalizatsiya va immobilayzer",
                        "en": "alarm and immobiliser"},
    "protection:tracker": {"ru": "спутниковый поиск", "uz": "sunʼiy yoʻldosh orqali qidiruv",
                           "en": "satellite tracking"},
    "seismic:z7": {"ru": "7 баллов", "uz": "7 ball", "en": "7 points"},
    "seismic:z8": {"ru": "8 баллов", "uz": "8 ball", "en": "8 points"},
    "seismic:z9": {"ru": "9 баллов", "uz": "9 ball", "en": "9 points"},
    "wear:new": {"ru": "до 10 лет", "uz": "10 yilgacha", "en": "up to 10 years"},
    "wear:mid": {"ru": "10–30 лет", "uz": "10–30 yil", "en": "10–30 years"},
    "wear:old": {"ru": "более 30 лет", "uz": "30 yildan ortiq", "en": "over 30 years"},
    "loss_history:clean": {"ru": "убытков не было", "uz": "zararlar boʻlmagan", "en": "no losses"},
    "loss_history:one": {"ru": "один убыток", "uz": "bitta zarar", "en": "one loss"},
    "loss_history:many": {"ru": "два и более", "uz": "ikki va undan koʻp", "en": "two or more"},
    "franchise:f0": {"ru": "без франшизы", "uz": "franshizasiz", "en": "no deductible"},
    "franchise:f05": {"ru": "0,5 % страховой суммы", "uz": "sugʻurta summasining 0,5 %", "en": "0.5% of the sum insured"},
    "franchise:f1": {"ru": "1 % страховой суммы", "uz": "sugʻurta summasining 1 %", "en": "1% of the sum insured"},
    "franchise:f2": {"ru": "2 % страховой суммы", "uz": "sugʻurta summasining 2 %", "en": "2% of the sum insured"},
    "veh_age:a3": {"ru": "до 3 лет", "uz": "3 yilgacha", "en": "up to 3 years"},
    "veh_age:a7": {"ru": "3–7 лет", "uz": "3–7 yil", "en": "3–7 years"},
    "veh_age:a7p": {"ru": "старше 7 лет", "uz": "7 yildan katta", "en": "over 7 years"},
    "veh_age:a15": {"ru": "8–15 лет", "uz": "8–15 yil", "en": "8–15 years"},
    "veh_age:a15p": {"ru": "старше 15 лет", "uz": "15 yildan katta", "en": "over 15 years"},
    "veh_type:car": {"ru": "легковой", "uz": "yengil", "en": "passenger car"},
    "veh_type:truck": {"ru": "грузовой", "uz": "yuk", "en": "truck"},
    "veh_type:special": {"ru": "спецтехника", "uz": "maxsus texnika", "en": "special machinery"},
    "veh_type:ev": {"ru": "электромобиль", "uz": "elektromobil", "en": "electric car"},
    "antitheft:none": {"ru": "нет", "uz": "yoʻq", "en": "none"},
    "antitheft:alarm": {"ru": "сигнализация", "uz": "signalizatsiya", "en": "alarm"},
    "antitheft:immo": {"ru": "сигнализация и иммобилайзер", "uz": "signalizatsiya va immobilayzer",
                       "en": "alarm and immobiliser"},
    "antitheft:tracker": {"ru": "спутниковый поиск", "uz": "sunʼiy yoʻldosh orqali qidiruv", "en": "satellite tracking"},
    "drivers:limited": {"ru": "ограниченный список, стаж от 3 лет", "uz": "cheklangan roʻyxat, staj 3 yildan",
                        "en": "named drivers, 3+ years' experience"},
    "drivers:unlimited": {"ru": "без ограничений", "uz": "cheklovsiz", "en": "unlimited"},
    "drivers:young": {"ru": "есть водители до 22 лет или со стажем до 3 лет",
                      "uz": "22 yoshgacha yoki staji 3 yilgacha haydovchilar bor",
                      "en": "drivers under 22 or with under 3 years' experience"},
    "engine_hours:eh_low": {"ru": "до 2 000", "uz": "2 000 gacha", "en": "up to 2,000"},
    "engine_hours:eh_mid": {"ru": "2 000–6 000", "uz": "2 000–6 000", "en": "2,000–6,000"},
    "engine_hours:eh_high": {"ru": "6 000–12 000", "uz": "6 000–12 000", "en": "6,000–12,000"},
    "engine_hours:eh_max": {"ru": "свыше 12 000", "uz": "12 000 dan ortiq", "en": "over 12,000"},
    "engine_hours:eh_na": {"ru": "не применимо", "uz": "qoʻllanilmaydi", "en": "not applicable"},
    "spec_site:site_wh": {"ru": "склад, закрытое помещение, площадка предприятия",
                          "uz": "ombor, yopiq xona, korxona maydoni", "en": "warehouse, indoor, company yard"},
    "spec_site:site_agro": {"ru": "сельское хозяйство, поле", "uz": "qishloq xoʻjaligi, dala", "en": "agriculture, field"},
    "spec_site:site_build": {"ru": "строительная площадка", "uz": "qurilish maydoni", "en": "construction site"},
    "spec_site:site_road": {"ru": "дорожные работы в полосе движения", "uz": "harakat qatnovidagi yoʻl ishlari",
                            "en": "roadworks in traffic lanes"},
    "spec_site:site_quarry": {"ru": "карьер, шахта, рудник", "uz": "karyer, shaxta, kon", "en": "quarry, mine"},
    "spec_site:site_na": {"ru": "не применимо", "uz": "qoʻllanilmaydi", "en": "not applicable"},
    "spec_operator:op_staff": {"ru": "штатный оператор с удостоверением", "uz": "guvohnomali shtatdagi operator",
                               "en": "licensed staff operator"},
    "spec_operator:op_rent_crew": {"ru": "аренда с экипажем", "uz": "ekipaj bilan ijara", "en": "hired out with crew"},
    "spec_operator:op_rent": {"ru": "аренда без экипажа", "uz": "ekipajsiz ijara", "en": "hired out without crew"},
    "spec_operator:op_unknown": {"ru": "круг операторов не определён", "uz": "operatorlar doirasi aniqlanmagan",
                                 "en": "operators not defined"},
    "spec_operator:op_na": {"ru": "не применимо", "uz": "qoʻllanilmaydi", "en": "not applicable"},
    "spec_guard:guard_full": {"ru": "огороженная площадка, охрана и видеонаблюдение",
                              "uz": "oʻralgan maydon, qoʻriqlash va videokuzatuv",
                              "en": "fenced yard, guards and CCTV"},
    "spec_guard:guard_gps": {"ru": "спутниковый мониторинг и блокировка двигателя",
                             "uz": "sunʼiy yoʻldosh monitoringi va dvigatelni bloklash",
                             "en": "satellite monitoring and engine lock"},
    "spec_guard:guard_watch": {"ru": "сторож без технических средств", "uz": "texnik vositasiz qorovul",
                               "en": "watchman without technical means"},
    "spec_guard:guard_none": {"ru": "машина остаётся на объекте без охраны", "uz": "mashina obyektda qoʻriqlanmay qoladi",
                              "en": "machine left on site unguarded"},
    "spec_guard:guard_na": {"ru": "не применимо", "uz": "qoʻllanilmaydi", "en": "not applicable"},
}

# откуда взято значение фактора
AN_SOURCE_LABELS = {
    "input": {"ru": "введено сотрудником", "uz": "xodim kiritgan", "en": "entered by staff"},
    "document": {"ru": "из документа", "uz": "hujjatdan", "en": "from the document"},
    "photo": {"ru": "с фото", "uz": "suratdan", "en": "from the photo"},
    "plate": {"ru": "с заводской таблички", "uz": "zavod lavhachasidan", "en": "from the nameplate"},
    "marking": {"ru": "с маркировки", "uz": "yozuvdan", "en": "from the marking"},
    "text": {"ru": "по описанию объекта в документе (словарь), проверьте",
             "uz": "hujjatdagi obyekt tavsifi boʻyicha (lugʻat), tekshiring",
             "en": "from the object description in the document (dictionary), please check"},
    "kind": {"ru": "по виду объекта", "uz": "obyekt turi boʻyicha", "en": "from the object type"},
    "default": {"ru": "принято по умолчанию, уточните", "uz": "standart boʻyicha qabul qilindi, aniqlang",
                "en": "assumed by default, please clarify"},
    "not_set": {"ru": "не указано — множитель 1 (как у среднего объекта)",
                "uz": "koʻrsatilmagan — koeffitsiyent 1 (oʻrtacha obyekt kabi)",
                "en": "not stated — multiplier 1 (as an average object)"},
    "act_terms": {"ru": "условия акта: франшиза не применена", "uz": "dalolatnoma shartlari: franshiza qoʻllanilmagan",
                  "en": "report terms: no deductible applied"},
}

# факторы объекта по подгруппам класса (02.10.2026): группы и коэффициенты — factor_groups шаблона класса,
# экспертно, не калибровано; режим — настройка акта factors.mode (reference | apply)
TX_FACTORS = {
    "fa_title": {"ru": "Факторы объекта по подгруппам класса", "uz": "Klass kichik guruhlari boʻyicha obyekt omillari",
                 "en": "Object factors by class subgroup"},
    "fa_row": {"ru": "Факторы объекта", "uz": "Obyekt omillari", "en": "Object factors"},
    # фон региона к фактору (stat_ref, 02.10.2026): открытые наборы stat.uz, коэффициент не меняют
    "fa_stat_walls": {"ru": "По данным stat.uz: {region}, конец {period} года — стены из материалов «{what}» — {share} "
                            "жилищного фонда ({url}). Фон региона, коэффициент не меняет.",
                      "uz": "stat.uz maʼlumotlariga koʻra: {region}, {period} yil oxiri — devorlari «{what}» "
                            "boʻlgan uylar uy-joy fondining {share} qismi ({url}). Hudud foni, koeffitsiyentni "
                            "oʻzgartirmaydi.",
                      "en": "stat.uz data: {region}, end of {period} — walls of “{what}” make up {share} of the "
                            "housing stock ({url}). Regional background, does not change the coefficient."},
    "fa_stat_value": {"ru": "По данным stat.uz: {region}, конец {period} года — {what}: {share} ({url}). Фон региона, "
                            "коэффициент не меняет.",
                      "uz": "stat.uz maʼlumotlariga koʻra: {region}, {period} yil oxiri — {what}: {share} ({url}). "
                            "Hudud foni, koeffitsiyentni oʻzgartirmaydi.",
                      "en": "stat.uz data: {region}, end of {period} — {what}: {share} ({url}). Regional background, "
                            "does not change the coefficient."},
    "fa_stat_none": {"ru": "Фон региона по данным stat.uz ({group}) недоступен: {reason}.",
                     "uz": "stat.uz boʻyicha hudud foni ({group}) mavjud emas: {reason}.",
                     "en": "stat.uz regional background ({group}) is unavailable: {reason}."},
    "fa_stat_row": {"ru": "Фон региона (stat.uz): {group}", "uz": "Hudud foni (stat.uz): {group}",
                    "en": "Regional background (stat.uz): {group}"},
    "fa_row_value": {"ru": "{items}; итоговый множитель {mult}", "uz": "{items}; yakuniy koeffitsiyent {mult}",
                     "en": "{items}; overall multiplier {mult}"},
    "fa_row_none": {"ru": "не заполнены — ставка не меняется", "uz": "toʻldirilmagan — stavka oʻzgarmaydi",
                    "en": "not filled in — the rate does not change"},
    "fa_row_note_reference": {"ru": "режим справочно: с учётом факторов {rate}, премия {premium}; ставка акта не "
                                    "изменена; экспертно, не калибровано",
                              "uz": "maʼlumot uchun rejimi: omillar bilan {rate}, mukofot {premium}; dalolatnoma "
                                    "stavkasi oʻzgartirilmagan; ekspert baho, kalibrlanmagan",
                              "en": "reference mode: with the factors {rate}, premium {premium}; the report rate is "
                                    "unchanged; expert, not calibrated"},
    "fa_row_note_apply": {"ru": "режим применён: ставка акта учитывает факторы; экспертно, не калибровано",
                          "uz": "qoʻllangan rejim: dalolatnoma stavkasi omillarni hisobga oladi; ekspert baho, "
                                "kalibrlanmagan",
                          "en": "applied mode: the report rate includes the factors; expert, not calibrated"},
    "fa_row_note_statutory": {"ru": "обязательный вид: ставка по нормативному акту, факторы не применяются",
                              "uz": "majburiy tur: stavka normativ hujjat boʻyicha, omillar qoʻllanilmaydi",
                              "en": "compulsory class: the rate is set by regulation, factors do not apply"},
    "fa_row_note_no_rate": {"ru": "ставка не определена — факторы справочно",
                            "uz": "stavka aniqlanmagan — omillar maʼlumot uchun",
                            "en": "the rate is not defined — factors for reference"},
    "fa_head": {"ru": "Заполнено групп {n} из {total}; коэффициенты экспертные, не калиброваны (утверждает актуарий "
                      "страховщика).",
                "uz": "{total} guruhdan {n} tasi toʻldirilgan; koeffitsiyentlar ekspert baho, kalibrlanmagan "
                      "(sugʻurtalovchi aktuariysi tasdiqlaydi).",
                "en": "{n} of {total} groups filled in; the factors are expert estimates, not calibrated (to be "
                      "approved by the insurer’s actuary)."},
    "fa_line": {"ru": "{group}: {option} — × {coef} ({note})", "uz": "{group}: {option} — × {coef} ({note})",
                "en": "{group}: {option} — × {coef} ({note})"},
    "fa_line_sum": {"ru": "{group}: {option} — × {coef}, ставка {rate}, к премии {delta} ({note})",
                    "uz": "{group}: {option} — × {coef}, stavka {rate}, mukofotga {delta} ({note})",
                    "en": "{group}: {option} — × {coef}, rate {rate}, premium {delta} ({note})"},
    "fa_bound": {"ru": "Произведение {raw} вне границ {lo}–{hi} (настройка акта) — взят множитель {mult}: ставка {rate}, "
                       "к премии {delta}",
                 "uz": "Koʻpaytma {raw} {lo}–{hi} chegarasidan tashqarida (dalolatnoma sozlamasi) — {mult} olindi: "
                       "stavka {rate}, mukofotga {delta}",
                 "en": "The product {raw} is outside the bounds {lo}–{hi} (report setting) — multiplier {mult} used: "
                       "rate {rate}, premium {delta}"},
    "fa_min": {"ru": "Ставка с факторами ниже минимальной — применён минимум {min}, к премии {delta}",
               "uz": "Omillar bilan stavka eng kamidan past — minimum {min} qoʻllanildi, mukofotga {delta}",
               "en": "The rate with the factors is below the minimum — the minimum {min} applies, premium {delta}"},
    "fa_total": {"ru": "Итоговый множитель {mult}: ставка {base} → {rate}, премия {p0} → {p1} ({delta}).",
                 "uz": "Yakuniy koeffitsiyent {mult}: stavka {base} → {rate}, mukofot {p0} → {p1} ({delta}).",
                 "en": "Overall multiplier {mult}: rate {base} → {rate}, premium {p0} → {p1} ({delta})."},
    "fa_base_act": {"ru": "База — ставка акта {rate}.", "uz": "Asos — dalolatnoma stavkasi {rate}.",
                    "en": "Base — the report rate {rate}."},
    "fa_base_calc": {"ru": "База — тариф × поправка по уровню риска {rate} (до сравнения с минимумом).",
                     "uz": "Asos — tarif × xavf darajasi tuzatishi {rate} (minimum bilan solishtirishdan oldin).",
                     "en": "Base — tariff × risk-level adjustment {rate} (before the minimum check)."},
    "fa_mode_reference": {"ru": "Режим «справочно»: ставка и премия акта не меняются; ставка с учётом факторов — "
                                "отметка в вилке ставки.",
                          "uz": "«Maʼlumot uchun» rejimi: dalolatnoma stavkasi va mukofoti oʻzgarmaydi; omillar bilan "
                                "stavka — tarif oraligʻidagi belgi.",
                          "en": "“Reference” mode: the report rate and premium do not change; the rate with the "
                                "factors is a mark in the rate range."},
    "fa_mode_apply": {"ru": "Режим «применён»: ставка акта = тариф × поправка по уровню × множитель факторов, не ниже "
                            "минимальной; премия — от неё.",
                      "uz": "«Qoʻllangan» rejim: dalolatnoma stavkasi = tarif × daraja tuzatishi × omillar "
                            "koeffitsiyenti, eng kamidan past emas; mukofot — undan.",
                      "en": "“Applied” mode: report rate = tariff × level adjustment × factor multiplier, not below "
                            "the minimum; the premium follows from it."},
    "fa_statutory": {"ru": "Обязательный вид: ставка установлена нормативным актом — факторы объекта её не меняют "
                           "(влияют только на добровольное расширение).",
                     "uz": "Majburiy tur: stavka normativ hujjat bilan belgilangan — obyekt omillari uni oʻzgartirmaydi "
                           "(faqat ixtiyoriy kengaytirishga taʼsir qiladi).",
                     "en": "Compulsory class: the rate is set by regulation — object factors do not change it (they "
                           "only affect a voluntary extension)."},
    "fa_no_rate": {"ru": "Ставка акта не определена — факторы показаны справочно, без пересчёта.",
                   "uz": "Dalolatnoma stavkasi aniqlanmagan — omillar qayta hisoblanmasdan maʼlumot uchun koʻrsatilgan.",
                   "en": "The report rate is not defined — factors are shown for reference, without recalculation."},
    "fa_none": {"ru": "Ни одна группа не заполнена — ставка не меняется.",
                "uz": "Birorta guruh toʻldirilmagan — stavka oʻzgarmaydi.",
                "en": "No group is filled in — the rate does not change."},
    "fa_unfilled": {"ru": "Уточнить (ставку не меняет): {items}.", "uz": "Aniqlashtirish (stavkani oʻzgartirmaydi): "
                                                                         "{items}.",
                    "en": "To clarify (does not change the rate): {items}."},
    "fa_unfilled_title": {"ru": "Уточнить факторы объекта", "uz": "Obyekt omillarini aniqlashtirish",
                          "en": "Object factors to clarify"},
    "fa_part": {"ru": "Часть {n} (класс {cls}): {text}", "uz": "{n}-qism ({cls}-klass): {text}",
                "en": "Part {n} (class {cls}): {text}"},
    "fa_row_part": {"ru": "Факторы объекта, часть {n}", "uz": "Obyekt omillari, {n}-qism",
                    "en": "Object factors, part {n}"},
    "fa_s5_reference": {"ru": "Факторы объекта (справочно, экспертно): ставка с их учётом {rate}, премия {premium}; "
                              "ставка акта не изменена.",
                        "uz": "Obyekt omillari (maʼlumot uchun, ekspert baho): ular bilan stavka {rate}, mukofot "
                              "{premium}; dalolatnoma stavkasi oʻzgartirilmagan.",
                        "en": "Object factors (reference, expert): rate with them {rate}, premium {premium}; the report "
                              "rate is unchanged."},
    "fa_s5_apply": {"ru": "Ставка акта учитывает факторы объекта (множитель {mult}, экспертно, не калибровано).",
                    "uz": "Dalolatnoma stavkasi obyekt omillarini hisobga oladi (koeffitsiyent {mult}, ekspert baho, "
                          "kalibrlanmagan).",
                    "en": "The report rate includes the object factors (multiplier {mult}, expert, not calibrated)."},
    "how_factors": {"ru": "Факторы объекта × {fmult} (экспертно, не калибровано): {calc} → {rate}",
                    "uz": "Obyekt omillari × {fmult} (ekspert baho, kalibrlanmagan): {calc} → {rate}",
                    "en": "Object factors × {fmult} (expert, not calibrated): {calc} → {rate}"},
    "how_factors_min": {"ru": "Ставка с факторами ниже минимальной — применён минимум {min}",
                        "uz": "Omillar bilan stavka eng kamidan past — minimum {min} qoʻllanildi",
                        "en": "The rate with the factors is below the minimum — the minimum {min} applies"},
    "rf_m_factors": {"ru": "С учётом факторов объекта (справочно)", "uz": "Obyekt omillari bilan (maʼlumot uchun)",
                     "en": "With object factors (reference)"},
    "rf_n_factors": {"ru": "ставка акта × множитель факторов объекта {mult} (экспертно, не калибровано)",
                     "uz": "dalolatnoma stavkasi × obyekt omillari koeffitsiyenti {mult} (ekspert baho, kalibrlanmagan)",
                     "en": "report rate × object factor multiplier {mult} (expert, not calibrated)"},
    "rf_n_factors_min": {"ru": "ставка акта × множитель факторов {mult} ниже минимума — применён минимум",
                         "uz": "dalolatnoma stavkasi × omillar koeffitsiyenti {mult} minimumdan past — minimum "
                               "qoʻllanildi",
                         "en": "report rate × factor multiplier {mult} is below the minimum — the minimum applies"},
    "rf_src_factors": {"ru": "шаблон класса: группы факторов (экспертно)", "uz": "klass shabloni: omillar guruhlari "
                                                                                 "(ekspert baho)",
                       "en": "class template: factor groups (expert)"},
}

# регион, территория страхования, акт на трёх языках из одного снимка (02.10.2026)
TX_TERRITORY = {
    "territory": {"ru": "Территория страхования", "uz": "Sugʻurta hududi", "en": "Territory of insurance"},
    "reg_note_republic": {"ru": "вся республика: статистика по Республике Узбекистан, поправка региона 0",
                          "uz": "butun respublika: statistika Oʻzbekiston Respublikasi boʻyicha, hudud tuzatishi 0",
                          "en": "whole republic: statistics for the Republic of Uzbekistan, regional adjustment 0"},
    "reg_note_outside": {"ru": "территория вне Узбекистана: открытые данные не применяются, поправка региона 0; "
                               "территория — как введена",
                         "uz": "hudud Oʻzbekistondan tashqarida: ochiq maʼlumotlar qoʻllanilmaydi, hudud tuzatishi 0; "
                               "hudud — kiritilganidek",
                         "en": "territory outside Uzbekistan: open data does not apply, regional adjustment 0; "
                               "territory as entered"},
    "an_st_outside": {"ru": "Территория вне Узбекистана: открытые данные (stat.uz, data.egov.uz, НАПП по регионам) "
                            "не применяются",
                      "uz": "Hudud Oʻzbekistondan tashqarida: ochiq maʼlumotlar (stat.uz, data.egov.uz, hududlar "
                            "boʻyicha SBNA) qoʻllanilmaydi",
                      "en": "Territory outside Uzbekistan: open data (stat.uz, data.egov.uz, NAPP by region) does "
                            "not apply"},
    "an_st_republic_note": {"ru": "Выбрана вся республика: показатели даны по Республике Узбекистан, сравнения региона "
                                  "с республикой нет",
                            "uz": "Butun respublika tanlangan: koʻrsatkichlar Oʻzbekiston Respublikasi boʻyicha, "
                                  "hududni respublika bilan taqqoslash yoʻq",
                            "en": "Whole republic selected: indicators are for the Republic of Uzbekistan, no "
                                  "region-to-country comparison"},
    "rf_reg_r_republic": {"ru": "выбрана вся республика — сравнивать регион с республикой не с чем",
                          "uz": "butun respublika tanlangan — hududni respublika bilan taqqoslab boʻlmaydi",
                          "en": "the whole republic is selected — there is no region to compare with the country"},
    "rf_reg_r_outside": {"ru": "территория вне Узбекистана — открытые данные не применяются",
                         "uz": "hudud Oʻzbekistondan tashqarida — ochiq maʼlumotlar qoʻllanilmaydi",
                         "en": "territory outside Uzbekistan — open data does not apply"},
    "rf_per1000": {"ru": "на 1 000 жителей", "uz": "1 000 aholiga", "en": "per 1,000 people"},
    "rf_fy_period": {"ru": "{year} год", "uz": "{year} yil", "en": "{year}"},
    "ex_source_name": {"ru": "УзРТСБ (Узбекская республиканская товарно-сырьевая биржа)",
                       "uz": "UzRTXB (Oʻzbekiston respublika tovar-xomashyo birjasi)",
                       "en": "UzRCE (Uzbek Republican Commodity Exchange)"},
    "fa_note_expert": {"ru": "экспертно, не калибровано", "uz": "ekspert baho, kalibrlanmagan",
                       "en": "expert estimate, not calibrated"},
    "fa_stat_note": {"ru": "фон региона, коэффициент не меняет",
                     "uz": "hudud fon maʼlumoti, koeffitsiyentni oʻzgartirmaydi",
                     "en": "regional background, the factor is unchanged"},
    "fa_stat_formula_walls_share": {"ru": "доля = сумма наборов варианта / сумма пяти наборов материала стен × 100",
                                    "uz": "ulush = variant toʻplamlari yigʻindisi / devor materiali boʻyicha besh "
                                          "toʻplam yigʻindisi × 100",
                                    "en": "share = sum of the datasets of the option / sum of the five wall-material "
                                          "datasets × 100"},
    "fa_stat_formula_value": {"ru": "показатель источника как есть", "uz": "manba koʻrsatkichi oʻzgarishsiz",
                              "en": "source indicator as is"},
    "fa_stat_r_no_ref": {"ru": "у варианта нет ссылки на наборы stat.uz",
                         "uz": "variantda stat.uz toʻplamlariga havola yoʻq",
                         "en": "the option has no link to stat.uz datasets"},
    "fa_stat_r_region_unknown": {"ru": "регион акта не опознан — фон региона не показан",
                                 "uz": "dalolatnoma hududi aniqlanmadi — hudud fon maʼlumoti koʻrsatilmadi",
                                 "en": "the region of the report is not recognised — no regional background"},
    "fa_stat_r_no_data": {"ru": "в базе нет данных набора для региона",
                          "uz": "bazada hudud uchun toʻplam maʼlumoti yoʻq",
                          "en": "no dataset values for the region in the database"},
    "fa_stat_r_mixed": {"ru": "в варианте смешаны наборы стен и иные — доля не считается",
                        "uz": "variantda devor va boshqa toʻplamlar aralash — ulush hisoblanmaydi",
                        "en": "the option mixes wall and other datasets — no share is computed"},
    "fa_stat_r_db_error": {"ru": "таблица статистики не прочитана", "uz": "statistika jadvali oʻqilmadi",
                           "en": "the statistics table could not be read"},
    "fa_stat_r_old_act": {"ru": "акт сформирован до подключения фона stat.uz",
                          "uz": "dalolatnoma stat.uz fon maʼlumoti ulanishidan oldin tuzilgan",
                          "en": "the report was made before the stat.uz background was connected"},
}
