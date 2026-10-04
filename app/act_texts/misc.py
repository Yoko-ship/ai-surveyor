"""Прочее: несколько объектов в акте, автозаполнение по ТС.
Часть словаря текстов акта (app/act_texts): склеивается в TX в __init__.py в прежнем порядке."""

# автозаполнение по ТС в ответе /act/photos (02.10.2026, app/vehicle_prefill.py)
VEH_FIELD_LABELS = {
    "object_label": {"ru": "Объект (марка и модель)", "uz": "Obyekt (marka va model)", "en": "Object (make and model)"},
    "year": {"ru": "Год выпуска", "uz": "Ishlab chiqarilgan yil", "en": "Year of manufacture"},
    "class_fields.veh_group": {"ru": "Тип транспорта (для тарифа)", "uz": "Transport turi (tarif uchun)",
                               "en": "Vehicle type (for rating)"},
    "class_fields.fuel": {"ru": "Топливо и привод", "uz": "Yoqilgʻi va yuritma", "en": "Fuel and drive"},
    "characteristics": {"ru": "Характеристики (мощность, масса, объём, места)",
                        "uz": "Tavsiflar (quvvat, massa, hajm, oʻrinlar)",
                        "en": "Characteristics (power, mass, displacement, seats)"},
}

# несколько объектов в одном акте — парк ТС (02.10.2026, app/act.py _apply_objects, _objects_view)
TX_OBJECTS = {
    "obj_title": {"ru": "Объекты договора ({n})", "uz": "Shartnoma obyektlari ({n})", "en": "Objects of the contract ({n})"},
    "obj_col_n": {"ru": "№", "uz": "№", "en": "No."},
    "obj_col_object": {"ru": "Объект", "uz": "Obyekt", "en": "Object"},
    "obj_col_year": {"ru": "Год", "uz": "Yil", "en": "Year"},
    "obj_col_sum": {"ru": "Страховая сумма", "uz": "Sugʻurta summasi", "en": "Sum insured"},
    "obj_col_value": {"ru": "Стоимость", "uz": "Qiymati", "en": "Value"},
    "obj_col_level": {"ru": "Уровень риска", "uz": "Xavf darajasi", "en": "Risk level"},
    "obj_col_rate": {"ru": "Ставка", "uz": "Tarif", "en": "Rate"},
    "obj_col_premium": {"ru": "Премия", "uz": "Mukofot", "en": "Premium"},
    "obj_col_ratio": {"ru": "Сумма к стоимости", "uz": "Summaning qiymatga nisbati", "en": "Sum to value"},
    "obj_s1_par": {"ru": "Договор на {n} объектов: страховая сумма {sum}, стоимость {value}. У каждого объекта свой "
                         "уровень риска, ставка и премия; премия договора {premium} — сумма премий объектов.",
                   "uz": "{n} ta obyekt uchun shartnoma: sugʻurta summasi {sum}, qiymati {value}. Har bir obyektning "
                         "oʻz xavf darajasi, tarifi va mukofoti bor; shartnoma mukofoti {premium} — obyektlar "
                         "mukofotlari yigʻindisi.",
                   "en": "Contract for {n} objects: sum insured {sum}, value {value}. Each object has its own risk "
                         "level, rate and premium; the contract premium {premium} is the sum of the objects' "
                         "premiums."},
    "obj_sums_auto": {"ru": "Сумма и стоимость договора посчитаны по объектам.",
                      "uz": "Shartnoma summasi va qiymati obyektlar boʻyicha hisoblandi.",
                      "en": "The contract sum and value are computed from the objects."},
    "obj_s3_title": {"ru": "Сумма к стоимости по объектам", "uz": "Obyektlar boʻyicha summaning qiymatga nisbati",
                     "en": "Sum to value by object"},
    "obj_s3_par": {"ru": "Сумма к стоимости проверена по договору и по каждому из {n} объектов: недострахование — {under}, "
                         "превышение стоимости — {over} (ГК РУз, ст. 936, 938).",
                   "uz": "Summaning qiymatga nisbati shartnoma va {n} ta obyektning har biri boʻyicha tekshirildi: "
                         "toʻliq sugʻurtalanmagan — {under}, qiymatdan oshgan — {over} (OʻzR FK, 936, 938-moddalar).",
                   "en": "Sum to value checked for the contract and for each of the {n} objects: underinsured — "
                         "{under}, above value — {over} (Civil Code, Art. 936, 938)."},
    "obj_v_normal": {"ru": "{ratio} — в норме", "uz": "{ratio} — meʼyorda", "en": "{ratio} — normal"},
    "obj_v_under": {"ru": "{ratio} — недострахование", "uz": "{ratio} — toʻliq sugʻurtalanmagan",
                    "en": "{ratio} — underinsured"},
    "obj_v_over": {"ru": "{ratio} — выше стоимости", "uz": "{ratio} — qiymatdan yuqori", "en": "{ratio} — above value"},
    "obj_v_na": {"ru": "не проверяется", "uz": "tekshirilmaydi", "en": "not checked"},
    "obj_level_note": {"ru": "самый высокий из объектов (объект {n}); не калибровано",
                       "uz": "obyektlar ichida eng yuqorisi ({n}-obyekt); kalibrlanmagan",
                       "en": "the highest among the objects (object {n}); not calibrated"},
    "obj_rate_avg": {"ru": "Средняя ставка по объектам", "uz": "Obyektlar boʻyicha oʻrtacha tarif",
                     "en": "Average rate across objects"},
    "obj_rate_avg_note": {"ru": "справочно: сумма премий объектов / страховая сумма × 365 / {days}; ставка и минимум "
                                "проверены по каждому объекту",
                          "uz": "maʼlumot uchun: obyektlar mukofotlari yigʻindisi / sugʻurta summasi × 365 / {days}; "
                                "tarif va minimum har bir obyekt boʻyicha tekshirildi",
                          "en": "for reference: sum of the objects' premiums / sum insured × 365 / {days}; rate and "
                                "minimum checked for each object"},
    "obj_premium_note": {"ru": "сумма премий {n} объектов за {days} дн.", "uz": "{n} ta obyekt mukofotlari yigʻindisi, "
                                                                            "{days} kun",
                         "en": "sum of the premiums of {n} objects for {days} days"},
    "obj_s4_title": {"ru": "Ставка и премия по объектам", "uz": "Obyektlar boʻyicha tarif va mukofot",
                     "en": "Rate and premium by object"},
    "obj_line": {"ru": "Объект {n} ({label}): уровень {level}, ставка {rate}, премия {premium}",
                 "uz": "{n}-obyekt ({label}): daraja {level}, tarif {rate}, mukofot {premium}",
                 "en": "Object {n} ({label}): level {level}, rate {rate}, premium {premium}"},
    "obj_fr": {"ru": "с франшизой договора — {rate}, {premium}", "uz": "shartnoma franshizasi bilan — {rate}, {premium}",
               "en": "with the contract deductible — {rate}, {premium}"},
    "obj_fa": {"ru": "множитель факторов объекта {mult}", "uz": "obyekt omillari koeffitsiyenti {mult}",
               "en": "object factor multiplier {mult}"},
    "obj_below_min": {"ru": "запрошенная ставка {req} ниже минимальной {min}", "uz": "soʻralgan tarif {req} eng kami "
                                                                                     "{min} dan past",
                      "en": "requested rate {req} is below the minimum {min}"},
    "how_objects": {"ru": "Договор на {n} объектов: ставка считается по каждому объекту (тариф × поправка уровня × "
                          "факторы объекта, не ниже минимума); премия договора {premium} = сумма премий объектов; "
                          "средняя ставка {rate} — справочно",
                    "uz": "{n} ta obyekt uchun shartnoma: tarif har bir obyekt boʻyicha hisoblanadi (tarif × daraja "
                          "tuzatishi × obyekt omillari, minimumdan past emas); shartnoma mukofoti {premium} = obyektlar "
                          "mukofotlari yigʻindisi; oʻrtacha tarif {rate} — maʼlumot uchun",
                    "en": "Contract for {n} objects: the rate is computed for each object (tariff × level adjustment × "
                          "object factors, not below the minimum); contract premium {premium} = sum of the objects' "
                          "premiums; average rate {rate} — for reference"},
    "how_object_line": {"ru": "Объект {n} ({label}): уровень {level}, ставка {rate}, премия {premium}",
                        "uz": "{n}-obyekt ({label}): daraja {level}, tarif {rate}, mukofot {premium}",
                        "en": "Object {n} ({label}): level {level}, rate {rate}, premium {premium}"},
    "sc_w_obj_largest": {"ru": "самый крупный объект № {n} ({label}): одно событие — один объект",
                         "uz": "eng yirik {n}-obyekt ({label}): bitta hodisa — bitta obyekt",
                         "en": "the largest object No. {n} ({label}): one event — one object"},
    "sc_w_obj_sum": {"ru": "все объекты вместе: накопление (весь парк в одном месте)",
                     "uz": "barcha obyektlar birga: toʻplanish (butun park bir joyda)",
                     "en": "all objects together: accumulation (the whole fleet in one place)"},
    "obj_sc_na": {"ru": "Сценарий убытка не считается ни по одному объекту", "uz": "Zarar ssenariysi birorta obyekt "
                                                                                  "boʻyicha hisoblanmaydi",
                  "en": "No loss scenario is computed for any object"},
    "obj_sc_rule": {"ru": "PML и EML — по самому крупному объекту (одно событие — один объект), MFL — сумма по всем "
                          "объектам (накопление: весь парк в одном месте); экспертно, не калибровано",
                    "uz": "PML va EML — eng yirik obyekt boʻyicha (bitta hodisa — bitta obyekt), MFL — barcha "
                          "obyektlar yigʻindisi (toʻplanish: butun park bir joyda); ekspert baho, kalibrlanmagan",
                    "en": "PML and EML — for the largest object (one event — one object), MFL — sum over all objects "
                          "(accumulation: the whole fleet in one place); expert, not calibrated"},
    "obj_sc_largest": {"ru": "Самый крупный объект № {n} ({label}): PML {pml}, EML {eml}, MFL {mfl}",
                       "uz": "Eng yirik {n}-obyekt ({label}): PML {pml}, EML {eml}, MFL {mfl}",
                       "en": "Largest object No. {n} ({label}): PML {pml}, EML {eml}, MFL {mfl}"},
    "obj_sc_sum": {"ru": "Сумма по {k} объектам: PML {pml}, EML {eml}, MFL {mfl}",
                   "uz": "{k} ta obyekt yigʻindisi: PML {pml}, EML {eml}, MFL {mfl}",
                   "en": "Sum over {k} objects: PML {pml}, EML {eml}, MFL {mfl}"},
    "obj_sc_excluded": {"ru": "Объект {n}: сценарий не считается — в сумму не входит",
                        "uz": "{n}-obyekt: ssenariy hisoblanmaydi — yigʻindiga kirmaydi",
                        "en": "Object {n}: no scenario — not included in the sum"},
    "obj_n_unbound": {"ru": "Фото не привязаны к объектам (photo_ids): осмотр учтён одинаково для всех объектов — "
                            "привяжите снимки к объектам, чтобы уровень риска считался по своим фото",
                      "uz": "Suratlar obyektlarga bogʻlanmagan (photo_ids): koʻrik barcha obyektlar uchun bir xil "
                            "hisobga olindi — xavf darajasi oʻz suratlari boʻyicha hisoblanishi uchun suratlarni "
                            "obyektlarga bogʻlang",
                      "en": "Photos are not linked to objects (photo_ids): the inspection counts the same for all "
                            "objects — link photos to objects so that each risk level uses its own photos"},
    "obj_n_refs_unknown": {"ru": "У объектов {n} указаны файлы, которых нет в загрузке, — они не учтены",
                           "uz": "{n} obyektlarda yuklamada yoʻq fayllar koʻrsatilgan — ular hisobga olinmadi",
                           "en": "Objects {n} refer to files that are not in the upload — they are ignored"},
    "obj_n_franchise": {"ru": "Франшиза договора применена к каждому объекту тем же множителем ставки ({mult}); "
                              "франшиза суммой — проверьте её размер на один объект",
                        "uz": "Shartnoma franshizasi har bir obyektga bir xil tarif koeffitsiyenti ({mult}) bilan "
                              "qoʻllanildi; summa koʻrinishidagi franshiza — bitta obyekt uchun hajmini tekshiring",
                        "en": "The contract deductible is applied to each object with the same rate multiplier "
                              "({mult}); for a deductible in sums check its size per object"},
    "obj_n_sums_auto": {"ru": "Страховая сумма и стоимость договора посчитаны как сумма по объектам.",
                        "uz": "Shartnomaning sugʻurta summasi va qiymati obyektlar yigʻindisi sifatida hisoblandi.",
                        "en": "The contract sum insured and value are computed as the sum over the objects."},
    "c_obj_under": {"ru": "Объект {n} ({label}): недострахование — предупредить о пропорциональной выплате "
                          "(ГК РУз, ст. 936)",
                    "uz": "{n}-obyekt ({label}): toʻliq sugʻurtalanmagan — mutanosib toʻlov haqida ogohlantirish "
                          "(OʻzR FK, 936-modda)",
                    "en": "Object {n} ({label}): underinsurance — warn about proportional settlement (Civil Code, "
                          "Art. 936)"},
    "c_obj_over": {"ru": "Объект {n} ({label}): сумма выше стоимости — снизить до стоимости (ГК РУз, ст. 938)",
                   "uz": "{n}-obyekt ({label}): summa qiymatdan yuqori — qiymatgacha kamaytirish (OʻzR FK, 938-modda)",
                   "en": "Object {n} ({label}): sum above value — reduce to the value (Civil Code, Art. 938)"},
    "c_obj_high": {"ru": "Объект {n} ({label}): высокий уровень риска — проверить до полиса",
                   "uz": "{n}-obyekt ({label}): yuqori xavf darajasi — polisgacha tekshirish",
                   "en": "Object {n} ({label}): high risk level — check before the policy"},
    "c_obj_decline": {"ru": "Объект {n} ({label}): сработали все повышающие признаки — исключить объект из договора "
                            "или отказать по нему",
                      "uz": "{n}-obyekt ({label}): barcha oshiruvchi belgilar bor — obyektni shartnomadan chiqarish "
                            "yoki u boʻyicha rad etish",
                      "en": "Object {n} ({label}): all aggravating signs present — exclude the object from the "
                            "contract or decline it"},
    "c_obj_below_min": {"ru": "Объект {n} ({label}): запрошенная ставка {req} ниже минимальной {min}",
                        "uz": "{n}-obyekt ({label}): soʻralgan tarif {req} eng kami {min} dan past",
                        "en": "Object {n} ({label}): requested rate {req} is below the minimum {min}"},
    "c_obj_no_photos": {"ru": "Объект {n} ({label}): нет своих фото — осмотреть или привязать снимки",
                        "uz": "{n}-obyekt ({label}): oʻz suratlari yoʻq — koʻzdan kechirish yoki suratlarni bogʻlash",
                        "en": "Object {n} ({label}): no photos of its own — inspect or link photos"},
    "fa_row_obj": {"ru": "Факторы объекта {n} ({label})", "uz": "{n}-obyekt omillari ({label})",
                   "en": "Factors of object {n} ({label})"},
    "fa_obj": {"ru": "Объект {n}: {text}", "uz": "{n}-obyekt: {text}", "en": "Object {n}: {text}"},
    "rf_sum_objects": {"ru": "Договор на {n} объектов: вилка по договору справочно (сумма премий объектов по каждой "
                             "отметке) — минимум {min}, ставка акта {act}, с учётом региона и рынка {adj}.",
                       "uz": "{n} ta obyekt uchun shartnoma: shartnoma boʻyicha oraliq maʼlumot uchun (har bir belgi "
                             "boʻyicha obyektlar mukofotlari yigʻindisi) — minimum {min}, dalolatnoma tarifi {act}, "
                             "hudud va bozor bilan {adj}.",
                       "en": "Contract for {n} objects: contract range for reference (sum of the objects' premiums at "
                             "each mark) — minimum {min}, report rate {act}, with region and market {adj}."},
    "rf_n_objects_min": {"ru": "сумма премий объектов по минимальной ставке; ставка договора — справочно",
                         "uz": "obyektlar mukofotlari yigʻindisi minimal tarif boʻyicha; shartnoma tarifi — maʼlumot "
                               "uchun",
                         "en": "sum of the objects' premiums at the minimum rate; contract rate for reference"},
    "rf_n_objects_act": {"ru": "сумма премий объектов по ставкам акта; ставка договора — справочно",
                         "uz": "obyektlar mukofotlari yigʻindisi dalolatnoma tariflari boʻyicha; shartnoma tarifi — "
                               "maʼlumot uchun",
                         "en": "sum of the objects' premiums at the report rates; contract rate for reference"},
    "rf_n_objects_adjusted": {"ru": "сумма премий объектов с учётом региона и рынка; ставка договора — справочно",
                              "uz": "hudud va bozorni hisobga olgan obyektlar mukofotlari yigʻindisi; shartnoma "
                                    "tarifi — maʼlumot uchun",
                              "en": "sum of the objects' premiums with region and market; contract rate for "
                                    "reference"},
    "rf_n_objects_doc": {"ru": "ставка документа по договору в целом — сравнивать со средней по объектам",
                         "uz": "hujjatdagi umumiy shartnoma tarifi — obyektlar boʻyicha oʻrtacha bilan solishtiring",
                         "en": "the document's rate for the whole contract — compare with the average across "
                               "objects"},
}

TX_OBJECTS_SRC = {
    "rf_src_objects": {"ru": "объекты договора (сумма премий)", "uz": "shartnoma obyektlari (mukofotlar yigʻindisi)",
                       "en": "objects of the contract (sum of premiums)"},
    "obj_factors_title": {"ru": "Признаки (объект {n} — самый высокий уровень)",
                          "uz": "Belgilar ({n}-obyekt — eng yuqori daraja)",
                          "en": "Signs (object {n} — the highest level)"},
    "obj_analytics_note": {"ru": "Аналитика раздела 4 (риски, состав тарифа, рынок, статистика) посчитана по договору в "
                                 "целом — как по одному объекту на всю страховую сумму; ставка и премия акта — по "
                                 "каждому объекту (таблица «Ставка и премия по объектам»).",
                           "uz": "4-boʻlim tahlili (xavflar, tarif tarkibi, bozor, statistika) shartnoma boʻyicha "
                                 "umuman — butun sugʻurta summasiga bitta obyekt sifatida hisoblangan; dalolatnoma "
                                 "tarifi va mukofoti — har bir obyekt boʻyicha («Obyektlar boʻyicha tarif va mukofot» "
                                 "jadvali).",
                           "en": "The section 4 analytics (risks, tariff structure, market, statistics) is computed for "
                                 "the contract as a whole — as one object for the full sum insured; the report rate "
                                 "and premium are per object (table “Rate and premium by object”)."},
}

TX_PREFILL = {
    "prefill_photo_check": {"ru": "с фото, проверьте", "uz": "suratdan, tekshiring", "en": "from the photo, please check"},
    "prefill_passport_check": {"ru": "из техпаспорта, проверьте", "uz": "texpasportdan, tekshiring",
                               "en": "from the vehicle registration, please check"},
}
