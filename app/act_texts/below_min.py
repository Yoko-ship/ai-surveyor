"""Минимальная ставка страховщика, тип ставки, оценка заниженной ставки.
Часть словаря текстов акта (app/act_texts): склеивается в TX в __init__.py в прежнем порядке."""

# ---------- минимальная ставка страховщика, тип ставки, оценка заниженной ставки (01.10.2026) ----------
TX_BELOW_MIN = {
    "how_min_src": {"ru": "Источник минимальной ставки: {min_src}", "uz": "Eng kam stavka manbai: {min_src}",
                    "en": "Source of the minimum rate: {min_src}"},
    "how_premium_fixed": {"ru": "Премия = {sum} × {rate} (ставка на весь срок, без деления на срок) = {premium}",
                          "uz": "Mukofot = {sum} × {rate} (butun muddatga stavka, muddatga boʻlinmaydi) = {premium}",
                          "en": "Premium = {sum} × {rate} (rate for the whole term, not divided by the term) = "
                                "{premium}"},
    "how_rate_annual_equiv": {"ru": "Годовой эквивалент фиксированной ставки для сравнения с рынком: {rate} × 365 / "
                                    "{days} = {calc}",
                              "uz": "Bozor bilan solishtirish uchun qatʼiy stavkaning yillik ekvivalenti: {rate} × "
                                    "365 / {days} = {calc}",
                              "en": "Annual equivalent of the fixed rate for market comparison: {rate} × 365 / {days} "
                                    "= {calc}"},
    "rq_how_premium_fixed": {"ru": "Премия по тарифу запроса = {sum} × {rate} (ставка на весь срок) = {premium}",
                             "uz": "Soʻrov tarifi boʻyicha mukofot = {sum} × {rate} (butun muddatga stavka) = {premium}",
                             "en": "Premium at the request rate = {sum} × {rate} (rate for the whole term) = {premium}"},
    "ct_how_premium_fixed": {"ru": "Премия по тарифу договора = {sum} × {rate} (ставка на весь срок) = {premium}",
                             "uz": "Shartnoma tarifi boʻyicha mukofot = {sum} × {rate} (butun muddatga stavka) = "
                                   "{premium}",
                             "en": "Premium at the contract rate = {sum} × {rate} (rate for the whole term) = "
                                   "{premium}"},
    "rq_how_fixed": {"ru": "Ставка продукта — фиксированная, на весь срок: премия не делится на срок",
                     "uz": "Mahsulot stavkasi — qatʼiy, butun muddatga: mukofot muddatga boʻlinmaydi",
                     "en": "The product rate is fixed for the whole term: the premium is not divided by the term"},
    "ct_how_fixed": {"ru": "Ставка продукта — фиксированная, на весь срок: премия не делится на срок",
                     "uz": "Mahsulot stavkasi — qatʼiy, butun muddatga: mukofot muddatga boʻlinmaydi",
                     "en": "The product rate is fixed for the whole term: the premium is not divided by the term"},
    "rate_type_annual": {"ru": "годовая", "uz": "yillik", "en": "annual"},
    "rate_type_fixed": {"ru": "фиксированная (на весь срок)", "uz": "qatʼiy (butun muddatga)",
                        "en": "fixed (for the whole term)"},
    "rate_type_row": {"ru": "Тип ставки", "uz": "Stavka turi", "en": "Rate type"},
    "rate_annual_equiv_row": {"ru": "Годовой эквивалент ставки (для сравнения с рынком)",
                              "uz": "Stavkaning yillik ekvivalenti (bozor bilan solishtirish uchun)",
                              "en": "Annual equivalent of the rate (for market comparison)"},
    "rf_fixed_note": {"ru": "Ставка продукта фиксированная (на весь срок): отметки вилки — годовой эквивалент "
                            "(× 365 / {days}), премии — те же, что по фиксированной ставке; ставку акта вилка не меняет",
                      "uz": "Mahsulot stavkasi qatʼiy (butun muddatga): oraliq belgilari — yillik ekvivalent "
                            "(× 365 / {days}), mukofotlar qatʼiy stavka boʻyicha bilan bir xil; oraliq dalolatnoma "
                            "stavkasini oʻzgartirmaydi",
                      "en": "The product rate is fixed (for the whole term): the range marks are annual equivalents "
                            "(× 365 / {days}); premiums equal those at the fixed rate; the range does not change the "
                            "report rate"},
    # оценка заниженной ставки
    "bm_title": {"ru": "Ставка ниже минимальной: можно ли застраховать",
                 "uz": "Stavka eng kamidan past: sugʻurtalash mumkinmi",
                 "en": "Rate below the minimum: can the object be insured"},
    "bm_v_allowed": {"ru": "да", "uz": "ha", "en": "yes"},
    "bm_v_allowed_with_conditions": {"ru": "да, при условиях", "uz": "ha, shartlar bilan",
                                     "en": "yes, subject to conditions"},
    "bm_v_not_allowed": {"ru": "нет", "uz": "yoʻq", "en": "no"},
    "bm_src_employee": {"ru": "введена сотрудником", "uz": "xodim kiritgan", "en": "entered by the employee"},
    "bm_src_request": {"ru": "запрос филиала", "uz": "filial soʻrovi", "en": "branch request"},
    "bm_src_contract": {"ru": "договор страхования", "uz": "sugʻurta shartnomasi", "en": "insurance contract"},
    "bm_min_insurer": {"ru": "минимальная ставка страховщика", "uz": "sugʻurtalovchining eng kam stavkasi",
                       "en": "the insurer's minimum rate"},
    "bm_min_regulator": {"ru": "минимум по нормативному акту", "uz": "meʼyoriy hujjat boʻyicha minimum",
                         "en": "the statutory minimum"},
    "bm_head": {"ru": "Запрошенная ставка {req} ({src}) ниже минимальной {min} ({min_src}). Можно ли застраховать "
                      "объект по запрошенной ставке: {verdict}.",
                "uz": "Soʻralgan stavka {req} ({src}) eng kam stavka {min} ({min_src}) dan past. Obyektni soʻralgan "
                      "stavka boʻyicha sugʻurtalash mumkinmi: {verdict}.",
                "en": "The requested rate {req} ({src}) is below the minimum {min} ({min_src}). Can the object be "
                      "insured at the requested rate: {verdict}."},
    "bm_why_no": {"ru": "Почему нет: {why}.", "uz": "Nega yoʻq: {why}.", "en": "Why not: {why}."},
    "bm_why_cond": {"ru": "Почему только при условиях: {why}.", "uz": "Nega faqat shartlar bilan: {why}.",
                    "en": "Why only subject to conditions: {why}."},
    "bm_why_yes": {"ru": "Все проверенные доводы — «за»; отступление оформляет андеррайтер с полномочиями.",
                   "uz": "Tekshirilgan barcha dalillar — «yoqlab»; chetga chiqishni vakolatli anderrayter rasmiylashtiradi.",
                   "en": "All checked arguments are in favour; the deviation is approved by an authorised underwriter."},
    "bm_conds": {"ru": "Условия: {what}.", "uz": "Shartlar: {what}.", "en": "Conditions: {what}."},
    "bm_shortfall": {"ru": "Недобор премии за срок ({days} дн.) против минимальной ставки: {sum}.",
                     "uz": "Eng kam stavkaga nisbatan muddat ({days} kun) uchun mukofot kamomadi: {sum}.",
                     "en": "Premium shortfall for the term ({days} days) against the minimum rate: {sum}."},
    "bm_shortfall_fixed": {"ru": "Недобор премии за весь срок против минимальной ставки: {sum}.",
                           "uz": "Eng kam stavkaga nisbatan butun muddat uchun mukofot kamomadi: {sum}.",
                           "en": "Premium shortfall for the whole term against the minimum rate: {sum}."},
    "bm_note": {"ru": "Правило разработчика, не утверждено страховщиком; окончательное решение — андеррайтер. Пороги — "
                      "в настройках акта (below_min), экспертно, не калибровано.",
                "uz": "Ishlab chiquvchi qoidasi, sugʻurtalovchi tomonidan tasdiqlanmagan; yakuniy qaror — anderrayter. "
                      "Chegaralar — dalolatnoma sozlamalarida (below_min), ekspert baho, kalibrlanmagan.",
                "en": "Developer's rule, not approved by the insurer; the final decision is the underwriter's. "
                      "Thresholds are in the report settings (below_min), expert, not calibrated."},
    "bm_for": {"ru": "за", "uz": "yoqlab", "en": "for"},
    "bm_against": {"ru": "против", "uz": "qarshi", "en": "against"},
    "bm_line": {"ru": "{sign}: {text}", "uz": "{sign}: {text}", "en": "{sign}: {text}"},
    "bm_statutory": {"ru": "обязательный вид: тариф установлен нормативным актом — отступление невозможно",
                     "uz": "majburiy tur: tarif meʼyoriy hujjat bilan belgilangan — chetga chiqish mumkin emas",
                     "en": "compulsory class: the tariff is set by regulation — no deviation is possible"},
    "bm_level_low": {"ru": "уровень риска низкий", "uz": "xavf darajasi past", "en": "the risk level is low"},
    "bm_level_moderate": {"ru": "уровень риска умеренный — отступление только при условиях",
                          "uz": "xavf darajasi oʻrtacha — chetga chiqish faqat shartlar bilan",
                          "en": "the risk level is moderate — deviation only subject to conditions"},
    "bm_level_high": {"ru": "уровень риска высокий — отступление не допускается",
                      "uz": "xavf darajasi yuqori — chetga chiqishga yoʻl qoʻyilmaydi",
                      "en": "the risk level is high — no deviation is allowed"},
    "bm_losses_none": {"ru": "убытков за 3 года нет", "uz": "3 yil ichida zararlar yoʻq",
                       "en": "no losses in 3 years"},
    "bm_losses_some": {"ru": "убытков за 3 года: {n} — отступление допускается только без убытков",
                       "uz": "3 yil ichidagi zararlar: {n} — chetga chiqishga faqat zararsiz yoʻl qoʻyiladi",
                       "en": "losses in 3 years: {n} — deviation is allowed only with no losses"},
    "bm_losses": {"ru": "убытков за 3 года: {n} (порог «нет» — {block}) — отступление не допускается",
                  "uz": "3 yil ichidagi zararlar: {n} («yoʻq» chegarasi — {block}) — chetga chiqishga yoʻl qoʻyilmaydi",
                  "en": "losses in 3 years: {n} (the «no» threshold is {block}) — no deviation is allowed"},
    "bm_losses_unknown": {"ru": "убытки за 3 года не указаны — нужна справка об убытках",
                          "uz": "3 yil ichidagi zararlar koʻrsatilmagan — zararlar haqida maʼlumotnoma kerak",
                          "en": "losses for 3 years are not stated — a loss record is needed"},
    "bm_net_ok": {"ru": "запрошенная ставка {req} не ниже нетто-ставки расчётного модуля {net} — ожидаемый убыток "
                        "покрывается{cal}",
                  "uz": "soʻralgan stavka {req} hisob-kitob modulining netto-stavkasi {net} dan past emas — kutilgan "
                        "zarar qoplanadi{cal}",
                  "en": "the requested rate {req} is not below the rating module's net rate {net} — the expected "
                        "loss is covered{cal}"},
    "bm_net_below": {"ru": "запрошенная ставка {req} ниже нетто-ставки расчётного модуля {net} — ставка не покрывает "
                           "ожидаемый убыток{cal}",
                     "uz": "soʻralgan stavka {req} hisob-kitob modulining netto-stavkasi {net} dan past — stavka "
                           "kutilgan zararni qoplamaydi{cal}",
                     "en": "the requested rate {req} is below the rating module's net rate {net} — the rate does not "
                           "cover the expected loss{cal}"},
    "bm_net_below_expert": {"ru": "запрошенная ставка {req} ниже нетто-ставки расчётного модуля {net}; нетто-ставка "
                                  "экспертная, не калибрована на статистике компании — покрытие ожидаемого убытка "
                                  "проверяет андеррайтер",
                            "uz": "soʻralgan stavka {req} hisob-kitob modulining netto-stavkasi {net} dan past; "
                                  "netto-stavka ekspert baho, kompaniya statistikasida kalibrlanmagan — kutilgan "
                                  "zararning qoplanishini anderrayter tekshiradi",
                            "en": "the requested rate {req} is below the rating module's net rate {net}; the net rate "
                                  "is expert, not calibrated on company data — the underwriter checks whether the "
                                  "expected loss is covered"},
    "bm_net_unknown": {"ru": "нетто-ставка расчётного модуля не определена — покрытие ожидаемого убытка не подтверждено",
                       "uz": "hisob-kitob modulining netto-stavkasi aniqlanmagan — kutilgan zararning qoplanishi "
                             "tasdiqlanmagan",
                       "en": "the rating module's net rate is not determined — coverage of the expected loss is not "
                             "confirmed"},
    "bm_cal_expert": {"ru": " (нетто-ставка экспертная, не калибрована)", "uz": " (netto-stavka ekspert baho, "
                                                                                "kalibrlanmagan)",
                      "en": " (the net rate is expert, not calibrated)"},
    "bm_retention_ok": {"ru": "EML {eml} в пределах удержания {limit}", "uz": "EML {eml} ushlab qolish {limit} "
                                                                              "doirasida",
                        "en": "EML {eml} is within the retention {limit}"},
    "bm_retention_over": {"ru": "EML {eml} выше удержания {limit} (Положение № 1806, п. 15) — отступление не "
                                "допускается",
                          "uz": "EML {eml} ushlab qolish {limit} dan yuqori (1806-son Nizom, 15-band) — chetga "
                                "chiqishga yoʻl qoʻyilmaydi",
                          "en": "EML {eml} exceeds the retention {limit} (Regulation No. 1806, cl. 15) — no deviation "
                                "is allowed"},
    "bm_retention_unknown": {"ru": "удержание не определено — EML не с чем сравнить",
                             "uz": "ushlab qolish aniqlanmagan — EML ni solishtirib boʻlmaydi",
                             "en": "the retention is not determined — EML cannot be compared"},
    "bm_docs": {"ru": "документы на объект представлены", "uz": "obyekt hujjatlari taqdim etilgan",
                "en": "the object documents are provided"},
    "bm_no_docs": {"ru": "документы на объект не представлены — отступление не допускается",
                   "uz": "obyekt hujjatlari taqdim etilmagan — chetga chiqishga yoʻl qoʻyilmaydi",
                   "en": "the object documents are not provided — no deviation is allowed"},
    "bm_share_ok": {"ru": "запрошенная ставка — {share} минимальной (порог — не ниже {lim})",
                    "uz": "soʻralgan stavka — eng kam stavkaning {share} (chegara — {lim} dan past emas)",
                    "en": "the requested rate is {share} of the minimum (threshold — at least {lim})"},
    "bm_share_low": {"ru": "запрошенная ставка — {share} минимальной, ниже порога {lim} — отступление слишком большое",
                     "uz": "soʻralgan stavka — eng kam stavkaning {share}, {lim} chegarasidan past — chetga chiqish "
                           "juda katta",
                     "en": "the requested rate is {share} of the minimum, below the {lim} threshold — the deviation "
                           "is too large"},
    "bm_new": {"ru": "объект новый, документы есть", "uz": "obyekt yangi, hujjatlar bor",
               "en": "the object is new, documents are available"},
    "bm_not_new": {"ru": "объект не новый или год выпуска неизвестен — нужен повторный осмотр",
                   "uz": "obyekt yangi emas yoki ishlab chiqarilgan yili nomaʼlum — qayta koʻrik kerak",
                   "en": "the object is not new or the year is unknown — a repeat inspection is needed"},
    "bm_market_ok": {"ru": "рыночная ставка класса {market} выше запрошенной {req} в {ratio} раза (порог — не более "
                           "{lim})",
                     "uz": "sinfning bozor stavkasi {market} soʻralgan {req} dan {ratio} baravar yuqori (chegara — "
                           "{lim} dan oshmasin)",
                     "en": "the class market rate {market} is {ratio} times the requested {req} (threshold — at most "
                           "{lim})"},
    "bm_market_high": {"ru": "рыночная ставка класса {market} выше запрошенной {req} в {ratio} раза — больше порога "
                             "{lim}",
                       "uz": "sinfning bozor stavkasi {market} soʻralgan {req} dan {ratio} baravar yuqori — {lim} "
                             "chegarasidan koʻp",
                       "en": "the class market rate {market} is {ratio} times the requested {req} — above the {lim} "
                             "threshold"},
    "bm_lr_low": {"ru": "убыточность класса по НАПП {lr} — ниже порога {lim}",
                  "uz": "SHNMA boʻyicha sinf zararliligi {lr} — {lim} chegarasidan past",
                  "en": "the class loss ratio per NAPP is {lr} — below the {lim} threshold"},
    "bm_lr_high": {"ru": "убыточность класса по НАПП {lr} — не ниже порога {lim}",
                   "uz": "SHNMA boʻyicha sinf zararliligi {lr} — {lim} chegarasidan past emas",
                   "en": "the class loss ratio per NAPP is {lr} — not below the {lim} threshold"},
    "bm_market_no": {"ru": "ни рыночная ставка, ни убыточность класса не подтверждают заниженную ставку — «да» без "
                           "условий невозможно",
                     "uz": "na bozor stavkasi, na sinf zararliligi pasaytirilgan stavkani tasdiqlamaydi — shartsiz "
                           "«ha» boʻlmaydi",
                     "en": "neither the market rate nor the class loss ratio supports the reduced rate — an "
                           "unconditional «yes» is not possible"},
    "bm_market_unknown": {"ru": "данных рынка по классу нет — «да» без условий невозможно",
                          "uz": "sinf boʻyicha bozor maʼlumotlari yoʻq — shartsiz «ha» boʻlmaydi",
                          "en": "no market data for the class — an unconditional «yes» is not possible"},
    "bm_gap": {"ru": "ставка ниже минимальной на {gap} ({gap_rel} минимальной); недобор премии за срок ({days} дн.): "
                     "{shortfall}",
               "uz": "stavka eng kamidan {gap} ga past (eng kam stavkaning {gap_rel}); muddat ({days} kun) uchun "
                     "mukofot kamomadi: {shortfall}",
               "en": "the rate is {gap} below the minimum ({gap_rel} of the minimum); premium shortfall for the term "
                     "({days} days): {shortfall}"},
    "bm_gap_fixed": {"ru": "ставка ниже минимальной на {gap} ({gap_rel} минимальной); недобор премии за весь срок: "
                           "{shortfall}",
                     "uz": "stavka eng kamidan {gap} ga past (eng kam stavkaning {gap_rel}); butun muddat uchun "
                           "mukofot kamomadi: {shortfall}",
                     "en": "the rate is {gap} below the minimum ({gap_rel} of the minimum); premium shortfall for the "
                           "whole term: {shortfall}"},
    "cond_franchise": {"ru": "франшиза (размер — по таблице франшиз раздела 4)",
                       "uz": "franshiza (miqdori — 4-boʻlimdagi franshizalar jadvali boʻyicha)",
                       "en": "a deductible (amount per the deductible table in section 4)"},
    "cond_measures": {"ru": "предупредительные мероприятия (раздел 5)", "uz": "oldini olish choralari (5-boʻlim)",
                      "en": "preventive measures (section 5)"},
    "cond_territory": {"ru": "ограничение территории страхования адресом объекта",
                       "uz": "sugʻurta hududini obyekt manzili bilan cheklash",
                       "en": "the territory of cover limited to the object address"},
    "cond_reinspect": {"ru": "повторный осмотр объекта до выдачи полиса", "uz": "polis berilishidan oldin obyektni "
                                                                                 "qayta koʻrikdan oʻtkazish",
                       "en": "a repeat inspection of the object before the policy is issued"},
    "cond_docs_confirm": {"ru": "подтверждение документов на объект оригиналами",
                          "uz": "obyekt hujjatlarini asl nusxalar bilan tasdiqlash",
                          "en": "confirmation of the object documents by originals"},
    "cond_losses_cert": {"ru": "справка об убытках за 3 года", "uz": "3 yillik zararlar haqida maʼlumotnoma",
                         "en": "a loss record for 3 years"},
    "cond_net_confirm": {"ru": "подтверждение андеррайтером, что ставка покрывает ожидаемый убыток",
                         "uz": "stavka kutilgan zararni qoplashini anderrayter tasdiqlashi",
                         "en": "the underwriter's confirmation that the rate covers the expected loss"},
    "cond_uw_authority": {"ru": "решение андеррайтера с полномочиями по отступлению от минимальной ставки",
                          "uz": "eng kam stavkadan chetga chiqish vakolatiga ega anderrayter qarori",
                          "en": "a decision by an underwriter authorised to deviate from the minimum rate"},
    "c_below_min": {"ru": "Отступление от минимальной ставки: запрошенная {req} при минимуме {min} — {verdict} — "
                          "решение андеррайтера",
                    "uz": "Eng kam stavkadan chetga chiqish: soʻralgan {req}, minimum {min} — {verdict} — anderrayter "
                          "qarori",
                    "en": "Deviation from the minimum rate: requested {req} against the minimum {min} — {verdict} — "
                          "underwriter decision"},
    "bm_s5_not_allowed": {"ru": "По запрошенной ставке {req} принять объект нельзя (ниже минимальной {min}); "
                                "рекомендация акта относится к ставке акта {rate}.",
                          "uz": "Soʻralgan stavka {req} boʻyicha obyektni qabul qilib boʻlmaydi (eng kam {min} dan "
                                "past); dalolatnoma tavsiyasi dalolatnoma stavkasi {rate} ga tegishli.",
                          "en": "The object cannot be accepted at the requested rate {req} (below the minimum {min}); "
                                "the report recommendation refers to the report rate {rate}."},
    "bm_s5_allowed_with_conditions": {"ru": "По запрошенной ставке {req} (минимум {min}) — только при условиях "
                                            "раздела 4 и решении андеррайтера.",
                                      "uz": "Soʻralgan stavka {req} (minimum {min}) boʻyicha — faqat 4-boʻlim shartlari "
                                            "va anderrayter qarori bilan.",
                                      "en": "At the requested rate {req} (minimum {min}) — only subject to the "
                                            "conditions in section 4 and an underwriter decision."},
    "bm_s5_allowed": {"ru": "Отступление до запрошенной ставки {req} (минимум {min}) допустимо при решении андеррайтера "
                            "с полномочиями.",
                      "uz": "Soʻralgan stavka {req} gacha chetga chiqish (minimum {min}) vakolatli anderrayter qarori "
                            "bilan mumkin.",
                      "en": "Deviation to the requested rate {req} (minimum {min}) is acceptable with an authorised "
                            "underwriter's decision."},
    "sc_o_below_min": {"ru": "ставка ниже минимальной: можно ли принять", "uz": "stavka eng kamidan past: qabul "
                                                                                 "qilish mumkinmi",
                       "en": "rate below the minimum: acceptable"},
    "sc_v_bm_not_below": {"ru": "не ниже минимума", "uz": "minimumdan past emas", "en": "not below the minimum"},
    "sc_v_bm_none": {"ru": "запрошенной ставки нет", "uz": "soʻralgan stavka yoʻq", "en": "no requested rate"},
}
