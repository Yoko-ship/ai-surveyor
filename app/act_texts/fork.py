"""Вилка ставки: минимум → ставка акта → с учётом региона и рынка → рынок.
Часть словаря текстов акта (app/act_texts): склеивается в TX в __init__.py в прежнем порядке."""

# --------------------------------------------------------------------------- #
#  Вилка ставки (01.10.2026): минимум → ставка акта → с учётом региона и рынка → рынок
# --------------------------------------------------------------------------- #
FORK_POS_LABELS = {
    "below_min": {"ru": "ниже минимума", "uz": "minimumdan past", "en": "below the minimum"},
    "inside": {"ru": "внутри вилки", "uz": "oraliq ichida", "en": "within the range"},
    "above_market": {"ru": "выше рынка", "uz": "bozordan yuqori", "en": "above the market"},
    "none": {"ru": "нет", "uz": "yoʻq", "en": "none"},
}

FORK_WHY_LABELS = {
    "kind": {"ru": "к этому виду объекта не относится", "uz": "bu obyekt turiga taalluqli emas",
             "en": "does not apply to this kind of object"},
    "no_regional": {"ru": "нет разреза по регионам — только республика", "uz": "hududlar kesimi yoʻq — faqat respublika",
                    "en": "no regional breakdown — country only"},
    "no_data": {"ru": "нет данных", "uz": "maʼlumot yoʻq", "en": "no data"},
    "region_unknown": {"ru": "регион не распознан", "uz": "hudud aniqlanmadi", "en": "region not recognised"},
    # вес 0 в настройке rate_fork.region.weights (claims_freq по умолчанию, 01.10.2026)
    "weight_zero": {"ru": "показан справочно, в поправку не входит (вес 0 в настройке)",
                    "uz": "maʼlumot uchun koʻrsatilgan, tuzatishga kirmaydi (sozlamada vazn 0)",
                    "en": "shown for reference, not part of the adjustment (weight 0 in the settings)"},
}

# регион «вся республика» и «другое» (02.10.2026): показатель региона в поправку не входит
FORK_WHY_LABELS.update({
    "republic": {"ru": "выбрана вся республика — сравнивать регион с республикой не с чем",
                 "uz": "butun respublika tanlangan — hududni respublika bilan taqqoslab boʻlmaydi",
                 "en": "the whole republic is selected — there is no region to compare with the country"},
    "outside": {"ru": "территория вне Узбекистана — открытые данные не применяются",
                "uz": "hudud Oʻzbekistondan tashqarida — ochiq maʼlumotlar qoʻllanilmaydi",
                "en": "territory outside Uzbekistan — open data does not apply"},
})

TX_FORK = {
    "rf_title": {"ru": "Вилка ставки", "uz": "Tarif oraligʻi", "en": "Rate range"},
    "rf_unit": {"ru": "% годовых", "uz": "yillik %", "en": "% per annum"},
    "rf_col_mark": {"ru": "Отметка", "uz": "Belgi", "en": "Mark"},
    "rf_col_rate": {"ru": "Ставка, % годовых", "uz": "Tarif, yillik %", "en": "Rate, % p.a."},
    "rf_col_premium": {"ru": "Премия за срок", "uz": "Muddat uchun mukofot", "en": "Premium for the term"},
    "rf_col_why": {"ru": "Из чего сложилась", "uz": "Nimadan tashkil topgan", "en": "How it is built"},
    "rf_col_src": {"ru": "Источник", "uz": "Manba", "en": "Source"},
    "rf_m_min": {"ru": "Минимальная", "uz": "Minimal", "en": "Minimum"},
    "rf_m_act": {"ru": "Ставка акта", "uz": "Dalolatnoma tarifi", "en": "Report rate"},
    "rf_m_adjusted": {"ru": "С учётом региона и рынка", "uz": "Hudud va bozorni hisobga olgan holda",
                      "en": "Adjusted for region and market"},
    "rf_m_market": {"ru": "Рыночная (НАПП)", "uz": "Bozor (SHNMA)", "en": "Market (NAPP)"},
    "rf_m_request": {"ru": "Запрос филиала", "uz": "Filial soʻrovi", "en": "Branch request"},
    "rf_m_contract": {"ru": "Договор", "uz": "Shartnoma", "en": "Contract"},
    "rf_m_technical": {"ru": "Техническая ставка расчётного модуля (справочно)",
                       "uz": "Hisob-kitob modulining texnik tarifi (maʼlumot uchun)",
                       "en": "Rating module technical rate (for reference)"},
    "rf_recommended": {"ru": "рекомендуем", "uz": "tavsiya etamiz", "en": "recommended"},
    "rf_rec_short": {"ru": "рек.", "uz": "tavs.", "en": "rec."},
    "rf_doc_policy": {"ru": "Тарифная политика INSON", "uz": "INSON tarif siyosati", "en": "INSON tariff policy"},
    "rf_doc_regulator": {"ru": "Тарифы регулятора", "uz": "Regulyator tariflari", "en": "Regulator's tariffs"},
    "rf_doc_regulation": {"ru": "нормативный акт", "uz": "normativ hujjat", "en": "regulation"},
    "rf_an_applied": {"ru": "с поправками региона и рынка (вилка ставки, режим «применить»)",
                      "uz": "hudud va bozor tuzatishlari bilan (tarif oraligʻi, «qoʻllash» rejimi)",
                      "en": "with region and market adjustments (rate range, “apply” mode)"},
    "rf_n_min": {"ru": "минимальная ставка продукта по тарифной политике; ниже — только отступление (решение андеррайтера)",
                 "uz": "tarif siyosati boʻyicha mahsulotning minimal tarifi; undan past — faqat chetlanish (anderrayter "
                       "qarori)",
                 "en": "the product's minimum rate under the tariff policy; below it — only by a deviation "
                       "(underwriter's decision)"},
    "rf_n_act": {"ru": "ставка тарифной политики × поправка по уровню риска {adj} (экспертно), не ниже минимума",
                 "uz": "tarif siyosati tarifi × xavf darajasi boʻyicha tuzatish {adj} (ekspert baho), minimumdan past emas",
                 "en": "tariff-policy rate × risk-level adjustment {adj} (expert), not below the minimum"},
    "rf_n_adjusted": {"ru": "ставка акта × (1 {reg}) × (1 {mkt}): поправки региона и рынка (экспертно, не калибровано)",
                      "uz": "dalolatnoma tarifi × (1 {reg}) × (1 {mkt}): hudud va bozor tuzatishlari (ekspert baho, "
                            "kalibrlanmagan)",
                      "en": "report rate × (1 {reg}) × (1 {mkt}): region and market adjustments (expert, not calibrated)"},
    "rf_n_adjusted_min": {"ru": "ставка акта × (1 {reg}) × (1 {mkt}) ниже минимума — применён минимум",
                          "uz": "dalolatnoma tarifi × (1 {reg}) × (1 {mkt}) minimumdan past — minimum qoʻllanildi",
                          "en": "report rate × (1 {reg}) × (1 {mkt}) is below the minimum — the minimum applies"},
    "rf_n_market": {"ru": "средняя ставка рынка по классу: премии / страховые обязательства (срез {date}); верхний "
                          "ориентир", "uz": "klass boʻyicha bozorning oʻrtacha tarifi: mukofotlar / sugʻurta "
                                           "majburiyatlari ({date} kesimi); yuqori moʻljal",
                    "en": "average market rate for the class: premiums / insurance liabilities (snapshot {date}); "
                          "upper benchmark"},
    "rf_n_market_below_min": {"ru": "средняя ставка рынка (срез {date}) ниже минимальной ставки продукта — это ориентир, "
                                    "вилку не меняет",
                              "uz": "bozorning oʻrtacha tarifi ({date} kesimi) mahsulotning minimal tarifidan past — bu "
                                    "moʻljal, oraliqni oʻzgartirmaydi",
                              "en": "the average market rate (snapshot {date}) is below the product minimum — a benchmark "
                                    "only, it does not change the range"},
    "rf_n_pos_below_min": {"ru": "ниже минимума — нужно отступление (решение андеррайтера)",
                           "uz": "minimumdan past — chetlanish kerak (anderrayter qarori)",
                           "en": "below the minimum — a deviation is needed (underwriter's decision)"},
    "rf_n_pos_inside": {"ru": "внутри вилки (не ниже минимума и не выше рынка)",
                        "uz": "oraliq ichida (minimumdan past emas va bozordan yuqori emas)",
                        "en": "within the range (not below the minimum, not above the market)"},
    "rf_n_pos_above_market": {"ru": "выше рыночной ставки", "uz": "bozor tarifidan yuqori",
                              "en": "above the market rate"},
    "rf_n_technical": {"ru": "справочно: база × доли рисков × коэффициенты, с нагрузкой; в премию акта не идёт",
                       "uz": "maʼlumot uchun: baza × xavflar ulushi × koeffitsiyentlar, yuklama bilan; dalolatnoma "
                             "mukofotiga kirmaydi",
                       "en": "for reference: base × peril shares × factors, with loading; not used in the report premium"},
    "rf_n_statutory": {"ru": "ставка установлена нормативным актом — без поправок",
                       "uz": "tarif normativ hujjat bilan belgilangan — tuzatishlarsiz",
                       "en": "the rate is set by a regulation — no adjustments"},
    "rf_n_parts_min": {"ru": "сумма премий частей по минимальным ставкам; ставка договора — справочно",
                       "uz": "qismlar mukofotlari yigʻindisi minimal tariflar boʻyicha; shartnoma tarifi — maʼlumot uchun",
                       "en": "sum of the parts' premiums at the minimum rates; contract rate for reference"},
    "rf_n_parts_act": {"ru": "сумма премий частей по ставкам акта; ставка договора — справочно",
                       "uz": "qismlar mukofotlari yigʻindisi dalolatnoma tariflari boʻyicha; shartnoma tarifi — maʼlumot "
                             "uchun",
                       "en": "sum of the parts' premiums at the report rates; contract rate for reference"},
    "rf_n_parts_adjusted": {"ru": "сумма премий частей с учётом региона и рынка; ставка договора — справочно",
                            "uz": "hudud va bozorni hisobga olgan qismlar mukofotlari yigʻindisi; shartnoma tarifi — "
                                  "maʼlumot uchun",
                            "en": "sum of the parts' premiums adjusted for region and market; contract rate for "
                                  "reference"},
    "rf_n_parts_doc": {"ru": "ставка документа по договору в целом — сравнивать с частями", "uz":
                       "hujjatdagi umumiy shartnoma tarifi — qismlar bilan solishtiring",
                       "en": "the document's rate for the whole contract — compare with the parts"},
    "rf_src_policy": {"ru": "{title}, действует с {date}", "uz": "{title}, {date} dan amalda",
                      "en": "{title}, effective from {date}"},
    "rf_src_policy_part": {"ru": "{title}: ставка части в тексте тарифа продукта {code}",
                           "uz": "{title}: {code} mahsulot tarifi matnidagi qism tarifi",
                           "en": "{title}: the part's rate in the tariff text of product {code}"},
    "rf_src_policy_act": {"ru": "{title} + поправка по уровню риска (настройки акта, экспертно)",
                          "uz": "{title} + xavf darajasi boʻyicha tuzatish (dalolatnoma sozlamalari, ekspert baho)",
                          "en": "{title} + risk-level adjustment (report settings, expert)"},
    "rf_src_napp": {"ru": "НАПП — отчёт о страховом рынке, срез {date}", "uz": "SHNMA — sugʻurta bozori hisoboti, "
                                                                                  "{date} kesimi",
                    "en": "NAPP — insurance market report, snapshot {date}"},
    "rf_src_adjusted": {"ru": "ставка акта + stat.uz / data.egov.uz + НАПП (поправки — ниже)",
                        "uz": "dalolatnoma tarifi + stat.uz / data.egov.uz + SHNMA (tuzatishlar — pastda)",
                        "en": "report rate + stat.uz / data.egov.uz + NAPP (adjustments below)"},
    "rf_src_request": {"ru": "запрос филиала", "uz": "filial soʻrovi", "en": "branch request"},
    "rf_src_contract": {"ru": "договор страхования", "uz": "sugʻurta shartnomasi", "en": "insurance contract"},
    "rf_src_technical": {"ru": "расчётный модуль (справочник базовых ставок и коэффициентов)",
                         "uz": "hisob-kitob moduli (bazaviy tariflar va koeffitsiyentlar maʼlumotnomasi)",
                         "en": "rating module (base rates and factors reference)"},
    "rf_src_statutory": {"ru": "нормативный акт: {title}", "uz": "normativ hujjat: {title}",
                         "en": "regulation: {title}"},
    "rf_src_parts": {"ru": "части договора (см. вилки частей)", "uz": "shartnoma qismlari (qismlar oraligʻiga qarang)",
                     "en": "contract parts (see the parts' ranges)"},
    "rf_src_line": {"ru": "Источник: {title} — {url}", "uz": "Manba: {title} — {url}", "en": "Source: {title} — {url}"},
    "rf_sum": {"ru": "Допустимо от {min} (минимум); рекомендуем {rec}; рынок {market}.",
               "uz": "{min} dan ruxsat etiladi (minimum); tavsiya etamiz {rec}; bozor {market}.",
               "en": "Acceptable from {min} (minimum); we recommend {rec}; market {market}."},
    "rf_sum_nomarket": {"ru": "Допустимо от {min} (минимум); рекомендуем {rec}; рыночной ставки по классу в данных НАПП "
                              "нет.",
                        "uz": "{min} dan ruxsat etiladi (minimum); tavsiya etamiz {rec}; SHNMA maʼlumotlarida klass "
                              "boʻyicha bozor tarifi yoʻq.",
                        "en": "Acceptable from {min} (minimum); we recommend {rec}; there is no market rate for the "
                              "class in the NAPP data."},
    "rf_sum_adj": {"ru": " С учётом региона и рынка — {adj} (справочно).",
                   "uz": " Hudud va bozorni hisobga olgan holda — {adj} (maʼlumot uchun).",
                   "en": " Adjusted for region and market — {adj} (for reference)."},
    "rf_sum_act": {"ru": " Ставка акта до поправок — {act}.", "uz": " Tuzatishlargacha dalolatnoma tarifi — {act}.",
                   "en": " Report rate before adjustments — {act}."},
    "rf_sum_doc": {"ru": " {what} {rate} — {pos}.", "uz": " {what} {rate} — {pos}.", "en": " {what} {rate} — {pos}."},
    "rf_sum_statutory": {"ru": "Тариф установлен нормативным актом: {rate} ({ref}) — вилки нет.",
                         "uz": "Tarif normativ hujjat bilan belgilangan: {rate} ({ref}) — oraliq yoʻq.",
                         "en": "The rate is set by a regulation: {rate} ({ref}) — no range."},
    "rf_sum_statutory_na": {"ru": "Тариф установлен нормативным актом ({ref}); числа ставки в справочнике нет — вилки "
                                  "нет.",
                            "uz": "Tarif normativ hujjat bilan belgilangan ({ref}); maʼlumotnomada tarif raqami yoʻq — "
                                  "oraliq yoʻq.",
                            "en": "The rate is set by a regulation ({ref}); the reference has no rate figure — no range."},
    "rf_sum_undefined": {"ru": "Ставка по продукту не определена (по программе, по согласованию или по генеральному "
                               "договору) — вилки нет; рыночный ориентир {market}.",
                         "uz": "Mahsulot boʻyicha tarif aniqlanmagan (dastur, kelishuv yoki bosh shartnoma boʻyicha) — "
                               "oraliq yoʻq; bozor moʻljali {market}.",
                         "en": "The product rate is not defined (by programme, by agreement or under a general "
                               "agreement) — no range; market benchmark {market}."},
    "rf_sum_undefined_nomarket": {"ru": "Ставка по продукту не определена (по программе, по согласованию или по "
                                        "генеральному договору) — вилки нет; рыночной ставки по классу нет.",
                                  "uz": "Mahsulot boʻyicha tarif aniqlanmagan (dastur, kelishuv yoki bosh shartnoma "
                                        "boʻyicha) — oraliq yoʻq; klass boʻyicha bozor tarifi yoʻq.",
                                  "en": "The product rate is not defined (by programme, by agreement or under a general "
                                        "agreement) — no range; there is no market rate for the class."},
    "rf_sum_parts": {"ru": "Договор из частей: вилка по каждой части (ниже); по договору справочно — минимум {min}, "
                           "ставка акта {act}, с учётом региона и рынка {adj}.",
                     "uz": "Qismlardan iborat shartnoma: har bir qism boʻyicha oraliq (pastda); shartnoma boʻyicha "
                           "maʼlumot uchun — minimum {min}, dalolatnoma tarifi {act}, hudud va bozor bilan {adj}.",
                     "en": "Contract made of parts: a range for each part (below); for the contract, for reference — "
                           "minimum {min}, report rate {act}, adjusted for region and market {adj}."},
    "rf_sum_error": {"ru": "Вилка ставки не посчитана (данные региона и рынка не прочитаны) — ставка акта не изменилась.",
                     "uz": "Tarif oraligʻi hisoblanmadi (hudud va bozor maʼlumotlari oʻqilmadi) — dalolatnoma tarifi "
                           "oʻzgarmadi.",
                     "en": "The rate range was not calculated (region and market data not read) — the report rate is "
                           "unchanged."},
    "rf_reg_title": {"ru": "Поправка региона: {pct}", "uz": "Hudud tuzatishi: {pct}", "en": "Region adjustment: {pct}"},
    "rf_reg_how": {"ru": "среднее по показателям (отношение «регион / республика» − 1) × {sens}, в границах от {lo} до "
                         "{hi}", "uz": "koʻrsatkichlar boʻyicha oʻrtacha (\"hudud / respublika\" nisbati − 1) × {sens}, "
                                       "{lo} dan {hi} gacha chegarada",
                   "en": "average over indicators of (region / country ratio − 1) × {sens}, bounded from {lo} to {hi}"},
    "rf_reg_raw": {"ru": "расчётная поправка {raw} ограничена границей {lim}",
                   "uz": "hisoblangan tuzatish {raw} {lim} chegarasi bilan cheklandi",
                   "en": "the calculated adjustment {raw} is capped at {lim}"},
    "rf_reg_ind": {"ru": "{name} ({period}): регион {reg}, республика {cty} {unit} — отношение {ratio} → {effect}",
                   "uz": "{name} ({period}): hudud {reg}, respublika {cty} {unit} — nisbat {ratio} → {effect}",
                   "en": "{name} ({period}): region {reg}, country {cty} {unit} — ratio {ratio} → {effect}"},
    "rf_reg_ind_off": {"ru": "{name}: не учтён — {why}", "uz": "{name}: hisobga olinmadi — {why}",
                       "en": "{name}: not used — {why}"},
    "rf_reg_none": {"ru": "нет данных по региону для этого вида объекта — поправка 0",
                    "uz": "bu obyekt turi uchun hudud boʻyicha maʼlumot yoʻq — tuzatish 0",
                    "en": "no regional data for this kind of object — adjustment 0"},
    "rf_reg_r_no_rules": {"ru": "для класса {cls} показатели региона в правило вилки не входят",
                          "uz": "{cls}-klass uchun hudud koʻrsatkichlari oraliq qoidasiga kirmaydi",
                          "en": "for class {cls} no regional indicators are part of the range rule"},
    "rf_reg_r_kind": {"ru": "показатели класса {cls} к этому виду объекта не относятся",
                      "uz": "{cls}-klass koʻrsatkichlari bu obyekt turiga taalluqli emas",
                      "en": "the class {cls} indicators do not apply to this kind of object"},
    "rf_reg_r_region_unknown": {"ru": "регион «{region}» не распознан", "uz": "«{region}» hududi aniqlanmadi",
                                "en": "region “{region}” not recognised"},
    "rf_reg_r_zero_weight": {"ru": "сравнение региона с республикой есть только у показателей с весом 0 в настройке",
                             "uz": "hududni respublika bilan taqqoslash faqat sozlamada vazni 0 boʻlgan "
                                   "koʻrsatkichlarda bor",
                             "en": "only indicators with weight 0 in the settings compare the region with the "
                                   "country"},
    "rf_reg_r_no_regional": {"ru": "у показателей нет разреза по регионам за общий период",
                             "uz": "koʻrsatkichlarda umumiy davr uchun hududlar kesimi yoʻq",
                             "en": "the indicators have no regional breakdown for a common period"},
    "rf_mkt_title": {"ru": "Поправка рынка: {pct}", "uz": "Bozor tuzatishi: {pct}", "en": "Market adjustment: {pct}"},
    "rf_mkt_applied": {"ru": "ставка акта {act} ниже рыночной {market}, убыточность класса {lr} (срез {date}) не ниже "
                             "порога {thr} → {pct}",
                       "uz": "dalolatnoma tarifi {act} bozor tarifi {market} dan past, klass zararliligi {lr} ({date} "
                             "kesimi) {thr} chegarasidan past emas → {pct}",
                       "en": "the report rate {act} is below the market {market}, the class loss ratio {lr} (snapshot "
                             "{date}) is at least {thr} → {pct}"},
    "rf_mkt_act_not_below": {"ru": "ставка акта {act} не ниже рыночной {market} (срез {date}) — поправки нет",
                             "uz": "dalolatnoma tarifi {act} bozor tarifi {market} dan past emas ({date} kesimi) — "
                                   "tuzatish yoʻq",
                             "en": "the report rate {act} is not below the market {market} (snapshot {date}) — "
                                   "no adjustment"},
    "rf_mkt_lr_below": {"ru": "ставка акта {act} ниже рыночной {market}, но убыточность класса {lr} (срез {date}) ниже "
                              "порога {thr} — поправки нет",
                        "uz": "dalolatnoma tarifi {act} bozor tarifi {market} dan past, lekin klass zararliligi {lr} "
                              "({date} kesimi) {thr} chegarasidan past — tuzatish yoʻq",
                        "en": "the report rate {act} is below the market {market}, but the class loss ratio {lr} "
                              "(snapshot {date}) is below the {thr} threshold — no adjustment"},
    "rf_mkt_no_data": {"ru": "нет данных НАПП по классу (рыночной ставки или убыточности) — поправка 0",
                       "uz": "klass boʻyicha SHNMA maʼlumoti yoʻq (bozor tarifi yoki zararlilik) — tuzatish 0",
                       "en": "no NAPP data for the class (market rate or loss ratio) — adjustment 0"},
    "rf_mkt_steps": {"ru": "ступени надбавки: {steps}; убыточность — выплаты / премии {basis}",
                     "uz": "ustama bosqichlari: {steps}; zararlilik — toʻlovlar / mukofotlar {basis}",
                     "en": "surcharge steps: {steps}; loss ratio — payouts / premiums {basis}"},
    "rf_mkt_step": {"ru": "убыточность от {thr} → {pct}", "uz": "zararlilik {thr} dan → {pct}",
                    "en": "loss ratio from {thr} → {pct}"},
    "rf_mkt_basis_last": {"ru": "последнего среза", "uz": "oxirgi kesim", "en": "of the latest snapshot"},
    "rf_mkt_basis_full_year": {"ru": "за полный год", "uz": "toʻliq yil uchun", "en": "for the full year"},
    "rf_mkt_pack": {"ru": "для классов 8 и 9 в отчёте НАПП одна строка «8, 9» — ставка и убыточность пакета",
                    "uz": "8 va 9-klasslar uchun SHNMA hisobotida bitta «8, 9» qatori — paket tarifi va zararliligi",
                    "en": "for classes 8 and 9 the NAPP report has one line “8, 9” — the package rate and loss ratio"},
    "rf_mode_reference": {"ru": "Режим «справочно»: премия акта считается по ставке акта; ставка с учётом региона и "
                                "рынка и премия по ней показаны рядом.",
                          "uz": "«Maʼlumot uchun» rejimi: dalolatnoma mukofoti dalolatnoma tarifi boʻyicha hisoblanadi; "
                                "hudud va bozorni hisobga olgan tarif va unga koʻra mukofot yonida koʻrsatilgan.",
                          "en": "“Reference” mode: the report premium is calculated at the report rate; the rate "
                                "adjusted for region and market and its premium are shown alongside."},
    "rf_mode_apply": {"ru": "Режим «применить»: ставка с учётом региона и рынка стала ставкой акта — премия, франшиза, "
                            "мероприятия и сверки посчитаны от неё.",
                      "uz": "«Qoʻllash» rejimi: hudud va bozorni hisobga olgan tarif dalolatnoma tarifiga aylandi — "
                            "mukofot, franshiza, tadbirlar va solishtirishlar undan hisoblangan.",
                      "en": "“Apply” mode: the rate adjusted for region and market became the report rate — the "
                            "premium, deductible, measures and checks are calculated from it."},
    "rf_fr_note": {"ru": "С учётом применённой франшизы премия акта — {premium}.",
                   "uz": "Qoʻllanilgan franshiza hisobga olinganda dalolatnoma mukofoti — {premium}.",
                   "en": "With the deductible applied, the report premium is {premium}."},
    "rf_calibrated": {"ru": "Чувствительность, границы и ступени — экспертные, не калиброваны (настройки акта, раздел "
                            "«Вилка ставки»).",
                      "uz": "Sezgirlik, chegaralar va bosqichlar — ekspert baho, kalibrlanmagan (dalolatnoma "
                            "sozlamalari, «Tarif oraligʻi» boʻlimi).",
                      "en": "Sensitivity, bounds and steps are expert values, not calibrated (report settings, "
                            "«Rate range» section)."},
    # потолок поправки рынка (01.10.2026): надбавка не поднимает ставку выше рыночной
    "rf_mkt_capped": {"ru": "надбавка рынка ограничена рыночной ставкой {market}: ставка с поправками не выше рыночной "
                            "(фактическая надбавка {pct})",
                      "uz": "bozor ustamasi bozor tarifi {market} bilan cheklangan: tuzatishli tarif bozor tarifidan "
                            "yuqori emas (amaldagi ustama {pct})",
                      "en": "the market loading is capped at the market rate {market}: the adjusted rate is not above "
                            "the market rate (actual loading {pct})"},
    "rf_mkt_cap_on": {"ru": "потолок — рыночная ставка: надбавка рынка не поднимает ставку выше неё",
                      "uz": "chegara — bozor tarifi: bozor ustamasi tarifni undan yuqori koʻtarmaydi",
                      "en": "ceiling — the market rate: the market loading does not lift the rate above it"},
    # оговорки показателей региона (01.10.2026): что именно измеряет показатель
    "rf_caveat": {"ru": "оговорка: {text}", "uz": "izoh: {text}", "en": "caveat: {text}"},
    "rf_row_adjusted": {"ru": "Ставка с учётом региона и рынка", "uz": "Hudud va bozorni hisobga olgan tarif",
                        "en": "Rate adjusted for region and market"},
    "rf_row_adjusted_note": {"ru": "справочно; премия {premium}", "uz": "maʼlumot uchun; mukofot {premium}",
                             "en": "for reference; premium {premium}"},
    "rf_row_act": {"ru": "Ставка акта до поправок региона и рынка", "uz": "Hudud va bozor tuzatishlarigacha "
                                                                         "dalolatnoma tarifi",
                   "en": "Report rate before region and market adjustments"},
    "rf_row_act_note": {"ru": "премия {premium}", "uz": "mukofot {premium}", "en": "premium {premium}"},
    "rf_parts_title": {"ru": "Вилка ставки по частям", "uz": "Qismlar boʻyicha tarif oraligʻi",
                       "en": "Rate range by part"},
    "rf_col_part": {"ru": "Часть", "uz": "Qism", "en": "Part"},
    "rf_contract_row": {"ru": "Договор (справочно)", "uz": "Shartnoma (maʼlumot uchun)", "en": "Contract (reference)"},
    "rf_part_line": {"ru": "{part}: {text}", "uz": "{part}: {text}", "en": "{part}: {text}"},
    "how_fork_region": {"ru": "Поправка региона {spct} (показатели stat.uz / data.egov.uz к республике, экспертно): "
                              "{base} → {rate}",
                        "uz": "Hudud tuzatishi {spct} (stat.uz / data.egov.uz koʻrsatkichlari respublikaga nisbatan, "
                              "ekspert baho): {base} → {rate}",
                        "en": "Region adjustment {spct} (stat.uz / data.egov.uz indicators against the country, expert): "
                              "{base} → {rate}"},
    "how_fork_market": {"ru": "Поправка рынка {spct} (НАПП: рыночная ставка и убыточность класса, экспертно): "
                              "{base} → {rate}",
                        "uz": "Bozor tuzatishi {spct} (SHNMA: klassning bozor tarifi va zararliligi, ekspert baho): "
                              "{base} → {rate}",
                        "en": "Market adjustment {spct} (NAPP: class market rate and loss ratio, expert): "
                              "{base} → {rate}"},
    "how_fork_market_cap": {"ru": "Надбавка рынка ограничена рыночной ставкой {market}: ставка не выше рыночной "
                                  "(настройка «потолок — рыночная ставка»)",
                            "uz": "Bozor ustamasi bozor tarifi {market} bilan cheklangan: tarif bozordan yuqori emas "
                                  "(«chegara — bozor tarifi» sozlamasi)",
                            "en": "The market loading is capped at the market rate {market}: the rate is not above "
                                  "the market (setting «ceiling — market rate»)"},
    "how_fork_min": {"ru": "Ставка с поправками ниже минимума — применён минимум {min}",
                     "uz": "Tuzatishlar bilan tarif minimumdan past — minimum {min} qoʻllanildi",
                     "en": "The adjusted rate is below the minimum — the minimum {min} applies"},
    "sc_o_fork": {"ru": "вилка ставки: минимум – акт – рынок", "uz": "tarif oraligʻi: minimum – dalolatnoma – bozor",
                  "en": "rate range: minimum – report – market"},
}
