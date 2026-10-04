"""Рынок: оценка по объявлениям, данные НАПП (пакеты классов, претензии), справка биржи УзРТСБ.
Часть словаря текстов акта (app/act_texts): склеивается в TX в __init__.py в прежнем порядке."""

# ================================================================================================
#  Оценка по объявлениям со снимков экрана сотрудника (30.09.2026)
# ================================================================================================
SITE_LABELS = {
    "olx": {"ru": "OLX", "uz": "OLX", "en": "OLX"},
    "avtoelon": {"ru": "Avtoelon.uz", "uz": "Avtoelon.uz", "en": "Avtoelon.uz"},
    "uybor": {"ru": "Uybor.uz", "uz": "Uybor.uz", "en": "Uybor.uz"},
    "joymee": {"ru": "Joymee.uz", "uz": "Joymee.uz", "en": "Joymee.uz"},
    "other": {"ru": "другая площадка", "uz": "boshqa sayt", "en": "another website"},
}

MV_VERDICT_LABELS = {
    "confirmed": {"ru": "стоимость подтверждена", "uz": "qiymat tasdiqlandi", "en": "value confirmed"},
    "refine": {"ru": "уточнить стоимость", "uz": "qiymatni aniqlashtirish", "en": "refine the value"},
    "few": {"ru": "мало объявлений — ориентировочно", "uz": "eʼlonlar kam — taxminiy",
            "en": "few listings — indicative"},
    "none": {"ru": "оценки нет", "uz": "baholash yoʻq", "en": "no valuation"},
    "ready": {"ru": "оценка готова к сравнению", "uz": "baholash solishtirishga tayyor",
              "en": "valuation ready for comparison"},
}

# Оговорки к строкам претензий НАПП (раздел 4 акта, /market/claims) — замечание контролёра 01.10.2026.
# market_picture.claims_caveats(lang, доля) подставляет долю города Ташкента в претензиях страны на срез.
CLAIMS_CAVEATS = {
    "not_events": {"ru": "претензии — заявления о выплате, а не страховые случаи: число случаев и их частоту в "
                         "актуарном смысле отчёт НАПП не даёт",
                   "uz": "daʼvolar — toʻlov haqidagi arizalar, sugʻurta hodisalari emas: SHNMA hisobotida hodisalar "
                         "soni va ularning aktuar maʼnodagi chastotasi yoʻq",
                   "en": "claims are applications for payment, not insured events: the NAPP report gives neither "
                         "the number of events nor their actuarial frequency"},
    "ytd_vs_date": {"ru": "в числителе — претензии с начала года, в знаменателе — договоры, действующие на дату "
                          "среза (договоры разной длительности, сезонность не учтена)",
                    "uz": "suratda — yil boshidan beri daʼvolar, maxrajda — kesim sanasida amaldagi shartnomalar "
                          "(shartnomalar muddati turlicha, mavsumiylik hisobga olinmagan)",
                    "en": "the numerator is claims since the start of the year, the denominator is contracts in "
                          "force on the snapshot date (contracts of different length, seasonality ignored)"},
    "payout_periods": {"ru": "средняя выплата — выплаты за период к числу оплаченных претензий за тот же период: "
                             "выплаты могут относиться к претензиям прошлых периодов",
                       "uz": "oʻrtacha toʻlov — davr uchun toʻlovlar shu davrda toʻlangan daʼvolar soniga: toʻlovlar "
                             "oʻtgan davrlar daʼvolariga tegishli boʻlishi mumkin",
                       "en": "the average payment is payouts for the period over claims paid in the same period: "
                             "payouts may relate to claims of earlier periods"},
    "capital": {"ru": "претензии и выплаты сосредоточены в городе Ташкенте — там головные офисы страховщиков и "
                      "онлайн-продажи: {share} претензий страны на этот срез",
                "uz": "daʼvolar va toʻlovlar Toshkent shahrida jamlangan — sugʻurtachilarning bosh ofislari va "
                      "onlayn savdo shu yerda: ushbu kesimda mamlakat daʼvolarining {share}",
                "en": "claims and payouts are concentrated in Tashkent city, where insurers' head offices and "
                      "online sales are: {share} of the country's claims in this snapshot"},
    "slices": {"ru": "значения «на 1 000 договоров» за срезы 3, 6, 9 и 12 месяцев между собой несопоставимы — "
                     "сравнивать можно только регион с республикой на одном срезе",
               "uz": "3, 6, 9 va 12 oylik kesimlardagi «1 000 shartnomaga» qiymatlarini oʻzaro solishtirib "
                     "boʻlmaydi — faqat bitta kesimda hududni respublika bilan solishtirish mumkin",
               "en": "“per 1,000 contracts” values of the 3-, 6-, 9- and 12-month snapshots are not comparable "
                     "with each other — compare only the region with the country within one snapshot"},
    "general_only": {"ru": "только общее страхование; разреза «регион × класс» и «страховщик × класс» в отчёте "
                           "НАПП нет",
                     "uz": "faqat umumiy sugʻurta; SHNMA hisobotida «hudud × klass» va «sugʻurtachi × klass» "
                           "kesimlari yoʻq",
                     "en": "general insurance only; the NAPP report has no “region × class” or “insurer × class” "
                           "breakdown"},
}

CLAIMS_CAVEAT_CAPITAL_NA = {
    "ru": "претензии и выплаты сосредоточены в городе Ташкенте — там головные офисы страховщиков и онлайн-продажи",
    "uz": "daʼvolar va toʻlovlar Toshkent shahrida jamlangan — sugʻurtachilarning bosh ofislari va onlayn savdo "
          "shu yerda",
    "en": "claims and payouts are concentrated in Tashkent city, where insurers' head offices and online sales are"}

TX_VALUATION = {
    # ---------- раздел 3: блок оценки ----------
    "mv_title": {"ru": "Оценка по объявлениям", "uz": "Eʼlonlar boʻyicha baholash", "en": "Valuation by listings"},
    "mv_median": {"ru": "Медиана цен объявлений", "uz": "Eʼlon narxlari medianasi", "en": "Median listing price"},
    "mv_range": {"ru": "Вилка (25–75-й перцентиль)", "uz": "Oraliq (25–75-persentil)",
                 "en": "Range (25th–75th percentile)"},
    "mv_range_val": {"ru": "от {low} до {high}", "uz": "{low} dan {high} gacha", "en": "{low} to {high}"},
    "mv_count": {"ru": "Объявлений в расчёте", "uz": "Hisobga olingan eʼlonlar", "en": "Listings used"},
    "mv_count_val": {"ru": "{used} из {count}", "uz": "{count} tadan {used} ta", "en": "{used} of {count}"},
    "mv_diff": {"ru": "Расхождение с заявленной стоимостью", "uz": "Koʻrsatilgan qiymat bilan farq",
                "en": "Difference from the declared value"},
    "mv_diff_up": {"ru": "заявленная выше медианы", "uz": "koʻrsatilgan qiymat medianadan yuqori",
                   "en": "declared value above the median"},
    "mv_diff_down": {"ru": "заявленная ниже медианы", "uz": "koʻrsatilgan qiymat medianadan past",
                     "en": "declared value below the median"},
    "mv_verdict": {"ru": "Вывод по объявлениям", "uz": "Eʼlonlar boʻyicha xulosa", "en": "Conclusion from listings"},
    "mvv_confirmed": {"ru": "Заявленная стоимость подтверждается объявлениями: расхождение {diff} — не больше порога "
                            "{thr}.",
                      "uz": "Koʻrsatilgan qiymat eʼlonlar bilan tasdiqlanadi: farq {diff} — {thr} chegarasidan "
                            "oshmaydi.",
                      "en": "The declared value is confirmed by the listings: the difference of {diff} does not "
                            "exceed the {thr} threshold."},
    "mvv_refine": {"ru": "Заявленная стоимость {declared} расходится с медианой объявлений {median} на {diff} — "
                         "больше порога {thr}. Уточнённая стоимость по объявлениям — {refined}; окончательную "
                         "стоимость стороны согласуют при заключении договора ({ref}).",
                   "uz": "Koʻrsatilgan qiymat {declared} eʼlonlar medianasi {median} dan {diff} ga farq qiladi — "
                         "{thr} chegarasidan koʻp. Eʼlonlar boʻyicha aniqlashtirilgan qiymat — {refined}; yakuniy "
                         "qiymatni tomonlar shartnoma tuzishda kelishadi ({ref}).",
                   "en": "The declared value of {declared} differs from the listing median of {median} by {diff}, "
                         "above the {thr} threshold. The refined value by listings is {refined}; the final value "
                         "is agreed by the parties when the contract is concluded ({ref})."},
    "mvv_few": {"ru": "Объявлений мало ({n}, нужно не меньше {min}) — оценка ориентировочная, заявленная стоимость по "
                      "ней не уточняется.",
                "uz": "Eʼlonlar kam ({n} ta, kamida {min} ta kerak) — baholash taxminiy, koʻrsatilgan qiymat unga "
                      "koʻra aniqlashtirilmaydi.",
                "en": "Too few listings ({n}, at least {min} needed) — the valuation is indicative and the "
                      "declared value is not refined by it."},
    "mvv_none": {"ru": "Подходящих объявлений нет — оценки по объявлениям нет.",
                 "uz": "Mos eʼlonlar yoʻq — eʼlonlar boʻyicha baholash yoʻq.",
                 "en": "There are no suitable listings — no valuation by listings."},
    "mvv_ready": {"ru": "Медиана посчитана; сравнение с заявленной стоимостью — в акте.",
                  "uz": "Mediana hisoblandi; koʻrsatilgan qiymat bilan solishtirish — dalolatnomada.",
                  "en": "The median has been calculated; the comparison with the declared value is in the report."},
    "mv_refined": {"ru": "Уточнённая стоимость (по объявлениям)", "uz": "Aniqlashtirilgan qiymat (eʼlonlar boʻyicha)",
                   "en": "Refined value (by listings)"},
    "mv_insured": {"ru": "Страховая сумма к уточнённой стоимости", "uz": "Sugʻurta summasining aniqlashtirilgan "
                                                                         "qiymatga nisbati",
                   "en": "Sum insured to refined value"},
    "mv_src_shots": {"ru": "Источник: {site}, объявления на {date}, снимки загружены сотрудником.",
                     "uz": "Manba: {site}, {date} holatidagi eʼlonlar, suratlarni xodim yuklagan.",
                     "en": "Source: {site}, listings as of {date}, screenshots uploaded by staff."},
    "mv_src_manual": {"ru": "Источник: введено сотрудником (объявлений: {n}).",
                      "uz": "Manba: xodim kiritgan (eʼlonlar: {n}).",
                      "en": "Source: entered by staff (listings: {n})."},
    "mv_src_edited": {"ru": "Исправлено сотрудником после чтения снимков: {k}.",
                      "uz": "Suratlar oʻqilgandan keyin xodim tuzatgan: {k}.",
                      "en": "Corrected by staff after reading the screenshots: {k}."},
    "mv_search": {"ru": "Ссылка поиска: {url}", "uz": "Qidiruv havolasi: {url}", "en": "Search link: {url}"},
    "mv_shots_missing": {"ru": "Снимки объявлений недоступны (прошло больше 24 часов или сменилась сессия) — "
                               "объявления учтены как введённые сотрудником.",
                         "uz": "Eʼlonlar suratlari mavjud emas (24 soatdan koʻp vaqt oʻtgan yoki sessiya "
                               "almashgan) — eʼlonlar xodim kiritgan deb hisobga olindi.",
                         "en": "The listing screenshots are unavailable (more than 24 hours have passed or the session "
                               "has changed) — the listings are treated as entered by staff."},
    "mv_edits_line": {"ru": "Правки сотрудника: снято {unchecked}, включено {checked}, исправлено цен "
                            "{price_changed}, убрано {removed}, добавлено вручную {manual}.",
                      "uz": "Xodim tuzatishlari: olib tashlangan {unchecked}, qoʻshilgan {checked}, narxi tuzatilgan "
                            "{price_changed}, roʻyxatdan olingan {removed}, qoʻlda qoʻshilgan {manual}.",
                      "en": "Staff edits: unticked {unchecked}, ticked {checked}, prices corrected {price_changed}, "
                            "removed {removed}, added manually {manual}."},
    "mv_edits_none": {"ru": "Правки сотрудника: правок нет.", "uz": "Xodim tuzatishlari: tuzatishlar yoʻq.",
                      "en": "Staff edits: none."},
    "mv_edits_title": {"ru": "Правки сотрудника в объявлениях", "uz": "Eʼlonlardagi xodim tuzatishlari",
                       "en": "Staff edits to the listings"},
    "mv_median_orig": {"ru": "без правок сотрудника — {median}", "uz": "xodim tuzatishlarisiz — {median}",
                       "en": "without the staff edits — {median}"},
    "mv_median_orig_none": {"ru": "без правок сотрудника оценки нет", "uz": "xodim tuzatishlarisiz baholash yoʻq",
                            "en": "no valuation without the staff edits"},
    "mv_orig_none": {"ru": "оценки нет", "uz": "baholash yoʻq", "en": "no valuation"},
    "me_price": {"ru": "цена исправлена сотрудником: было {was}, стало {now}",
                 "uz": "narxni xodim tuzatgan: {was} edi, {now} boʻldi",
                 "en": "price corrected by staff: was {was}, now {now}"},
    "me_currency": {"ru": "валюта исправлена сотрудником: было {was}, стало {now}",
                    "uz": "valyutani xodim tuzatgan: {was} edi, {now} boʻldi",
                    "en": "currency corrected by staff: was {was}, now {now}"},
    "me_year": {"ru": "год исправлен сотрудником: было {was}, стало {now}",
                "uz": "yilni xodim tuzatgan: {was} edi, {now} boʻldi",
                "en": "year corrected by staff: was {was}, now {now}"},
    "me_posted_date": {"ru": "дата публикации исправлена сотрудником: было {was}, стало {now}",
                       "uz": "eʼlon sanasini xodim tuzatgan: {was} edi, {now} boʻldi",
                       "en": "publication date corrected by staff: was {was}, now {now}"},
    "me_title": {"ru": "название исправлено сотрудником: было «{was}»",
                 "uz": "nomini xodim tuzatgan: «{was}» edi",
                 "en": "title corrected by staff: was \"{was}\""},
    "me_checked": {"ru": "включено сотрудником (модель исключила)", "uz": "xodim qoʻshgan (model chiqarib tashlagan)",
                   "en": "ticked by staff (the model had excluded it)"},
    "me_unchecked": {"ru": "снято сотрудником", "uz": "xodim olib tashlagan", "en": "unticked by staff"},
    "me_removed": {"ru": "убрано сотрудником из списка", "uz": "xodim roʻyxatdan olib tashlagan",
                   "en": "removed from the list by staff"},
    "me_manual": {"ru": "добавлено сотрудником вручную, цена {price}", "uz": "xodim qoʻlda qoʻshgan, narxi {price}",
                  "en": "added manually by staff, price {price}"},
    # раздел 3: один итоговый вывод при «уточнить стоимость»
    "vf_declared": {"ru": "К заявленной стоимости страховая сумма составляет {ratio} — {v}.",
                    "uz": "Koʻrsatilgan qiymatga nisbatan sugʻurta summasi {ratio} — {v}.",
                    "en": "Against the declared value the sum insured is {ratio} — {v}."},
    "vf_market": {"ru": "Но по объявлениям стоимость {dir} заявленной на {diff}: к уточнённой стоимости страховая "
                        "сумма составляет {ratio} — {v}.",
                  "uz": "Biroq eʼlonlar boʻyicha qiymat koʻrsatilganidan {diff} ga {dir}: aniqlashtirilgan qiymatga "
                        "nisbatan sugʻurta summasi {ratio} — {v}.",
                  "en": "But by the listings the value is {diff} {dir} than declared: against the refined value the "
                        "sum insured is {ratio} — {v}."},
    "vf_market_short": {"ru": "Но по объявлениям стоимость {dir} заявленной на {diff}.",
                        "uz": "Biroq eʼlonlar boʻyicha qiymat koʻrsatilganidan {diff} ga {dir}.",
                        "en": "But by the listings the value is {diff} {dir} than declared."},
    "vf_total": {"ru": "Итог: стоимость нужно уточнить.", "uz": "Xulosa: qiymatni aniqlashtirish kerak.",
                 "en": "Result: the value needs to be refined."},
    "vf_lower": {"ru": "ниже", "uz": "past", "en": "lower"},
    "vf_higher": {"ru": "выше", "uz": "yuqori", "en": "higher"},
    "mv_declared_orig": {"ru": "Заявлено клиентом", "uz": "Mijoz koʻrsatgan qiymat", "en": "Declared by the client"},
    "mv_declared_replaced": {"ru": "Заявлено клиентом: {orig}; стоимость принята по объявлениям: {value}.",
                             "uz": "Mijoz koʻrsatgan: {orig}; qiymat eʼlonlar boʻyicha qabul qilindi: {value}.",
                             "en": "Declared by the client: {orig}; the value was taken from the listings: {value}."},
    "mv_declared_changed": {"ru": "Заявлено клиентом: {orig}; стоимость объекта изменена сотрудником: {value}.",
                            "uz": "Mijoz koʻrsatgan: {orig}; obyekt qiymatini xodim oʻzgartirgan: {value}.",
                            "en": "Declared by the client: {orig}; the object value was changed by staff: {value}."},
    "mv_how_title": {"ru": "Как посчитана оценка по объявлениям", "uz": "Eʼlonlar boʻyicha baholash qanday hisoblandi",
                     "en": "How the valuation by listings was calculated"},
    "mv_excl_title": {"ru": "Не вошли в расчёт", "uz": "Hisobga kirmadi", "en": "Not included"},
    "mv_excl_item": {"ru": "{title} — {reason}", "uz": "{title} — {reason}", "en": "{title} — {reason}"},
    "mv_untitled": {"ru": "объявление {id}", "uz": "{id}-eʼlon", "en": "listing {id}"},
    "mx_not_relevant": {"ru": "другое изделие (марка, модель или тип не совпадают)",
                        "uz": "boshqa buyum (marka, model yoki tur mos emas)",
                        "en": "a different item (make, model or type do not match)"},
    "mx_unchecked_by_employee": {"ru": "снято сотрудником (модель считала объявление подходящим)",
                                 "uz": "xodim olib tashlagan (model eʼlonni mos deb hisoblagan)",
                                 "en": "unticked by staff (the model considered the listing suitable)"},
    "mx_removed_by_employee": {"ru": "убрано сотрудником из списка", "uz": "xodim roʻyxatdan olib tashlagan",
                               "en": "removed from the list by staff"},
    "mx_no_date": {"ru": "дата публикации не видна", "uz": "eʼlon sanasi koʻrinmaydi",
                   "en": "publication date not visible"},
    "mx_bad_date": {"ru": "дата публикации некорректна: позже даты снимков ({shot})",
                    "uz": "eʼlon sanasi notoʻgʻri: suratlar sanasidan ({shot}) keyin",
                    "en": "publication date is invalid: later than the screenshot date ({shot})"},
    "mx_no_price": {"ru": "цена не видна", "uz": "narx koʻrinmaydi", "en": "price not visible"},
    "mx_no_rate": {"ru": "цена в долларах, курс не задан", "uz": "narx dollarda, kurs berilmagan",
                   "en": "price in US dollars, no exchange rate given"},
    "mx_too_old": {"ru": "опубликовано {date} — старше {months} мес.", "uz": "{date} da eʼlon qilingan — {months} "
                                                                             "oydan eski",
                   "en": "published on {date} — older than {months} months"},
    "mx_outlier_low": {"ru": "выброс: {price} ниже {k} медианы {median}", "uz": "chetlanish: {price} mediana "
                                                                               "{median} ning {k} qismidan past",
                       "en": "outlier: {price} is below {k} of the median {median}"},
    "mx_outlier_high": {"ru": "выброс: {price} выше медианы {median} более чем в {k} раза",
                        "uz": "chetlanish: {price} mediana {median} dan {k} martadan koʻp yuqori",
                        "en": "outlier: {price} is more than {k} times the median {median}"},
    "mh_filter": {"ru": "Взяты объявления о том же изделии, с ценой и видимой датой публикации, не старше {months} мес. "
                        "от даты снимков; объявления без даты публикации в расчёт не берутся.",
                  "uz": "Xuddi shu buyum boʻyicha, narxi va eʼlon sanasi koʻrinib turgan, suratlar sanasidan {months} "
                        "oydan eski boʻlmagan eʼlonlar olindi; eʼlon sanasi yoʻq eʼlonlar hisobga olinmaydi.",
                  "en": "Listings for the same item with a price and a visible publication date, not older than "
                        "{months} months from the screenshot date were used; listings without a publication date "
                        "are not used."},
    "mh_filter_undated": {"ru": "Взяты объявления о том же изделии, с ценой, не старше {months} мес. от даты снимков; "
                                "если дата публикации не видна — принята дата снимка (с пометкой; так разрешено "
                                "настройкой оценки).",
                          "uz": "Xuddi shu buyum boʻyicha, narxi bor, suratlar sanasidan {months} oydan eski "
                                "boʻlmagan eʼlonlar olindi; eʼlon sanasi koʻrinmasa — surat sanasi olindi (belgi bilan; "
                                "baholash sozlamasi shunga ruxsat beradi).",
                          "en": "Listings for the same item, with a price, not older than {months} months from the "
                                "screenshot date were used; where the publication date is not visible, the screenshot "
                                "date was taken (flagged; allowed by the valuation setting)."},
    "mh_outliers_skipped": {"ru": "Подходящих объявлений {n} — меньше {min}: выбросы не искались.",
                            "uz": "Mos eʼlonlar {n} ta — {min} tadan kam: chetlanishlar qidirilmadi.",
                            "en": "{n} suitable listings — fewer than {min}: outliers were not searched for."},
    "mh_fx": {"ru": "Цены в долларах пересчитаны в сумы по курсу {rate} за 1 доллар.",
              "uz": "Dollardagi narxlar 1 dollar uchun {rate} kurs boʻyicha soʻmga oʻtkazildi.",
              "en": "US dollar prices were converted into UZS at {rate} per 1 US dollar."},
    "mh_fx_none": {"ru": "Курс доллара не получен и не введён — объявления в долларах в расчёт не вошли; курс не "
                         "выдумывается.",
                   "uz": "Dollar kursi olinmadi va kiritilmadi — dollardagi eʼlonlar hisobga kirmadi; kurs "
                         "oʻylab topilmaydi.",
                   "en": "No US dollar rate was obtained or entered — listings in US dollars were not used; the "
                         "rate is never guessed."},
    "mh_none": {"ru": "Подходящих объявлений с ценой нет — медиана не считается.",
                "uz": "Narxi bor mos eʼlonlar yoʻq — mediana hisoblanmaydi.",
                "en": "There are no suitable listings with a price — no median is calculated."},
    "mh_outliers": {"ru": "Выбросы — ниже {lo} и выше {hi} медианы отобранных ({med0}) — отброшены: {k}.",
                    "uz": "Chetlanishlar — tanlanganlar medianasi ({med0}) ning {lo} qismidan past va {hi} "
                          "martadan yuqori — chiqarildi: {k} ta.",
                    "en": "Outliers — below {lo} or above {hi} times the median of the selected listings "
                          "({med0}) — removed: {k}."},
    "mh_median": {"ru": "Медиана {n} цен — {median}; вилка (25-й и 75-й перцентили) — от {low} до {high}.",
                  "uz": "{n} ta narx medianasi — {median}; oraliq (25- va 75-persentillar) — {low} dan {high} gacha.",
                  "en": "Median of {n} prices — {median}; range (25th and 75th percentiles) — {low} to {high}."},
    "mh_dates_assumed": {"ru": "Дата публикации не видна у объявлений: {k} — принята дата снимка.",
                         "uz": "{k} ta eʼlonda eʼlon sanasi koʻrinmaydi — surat sanasi olindi.",
                         "en": "The publication date is not visible for {k} listings — the screenshot date was used."},
    "mh_no_adj": {"ru": "Поправки на год и пробег не применялись: в методике оценки готовое правило есть только для "
                        "метода износа, для объявлений его нет.",
                  "uz": "Yil va yurgan masofa boʻyicha tuzatishlar qoʻllanmadi: baholash metodikasida tayyor qoida "
                        "faqat eskirish usuli uchun bor, eʼlonlar uchun yoʻq.",
                  "en": "No adjustments for year or mileage were made: the valuation method has a ready rule only "
                        "for the depreciation method, not for listings."},
    "mh_compare": {"ru": "Заявленная стоимость {declared} отличается от медианы {median} на {diff} (порог {thr}); "
                         "расхождение считается от заявленной стоимости.",
                   "uz": "Koʻrsatilgan qiymat {declared} mediana {median} dan {diff} ga farq qiladi (chegara {thr}); "
                         "farq koʻrsatilgan qiymatdan hisoblanadi.",
                   "en": "The declared value of {declared} differs from the median of {median} by {diff} "
                         "(threshold {thr}); the difference is measured against the declared value."},
    "mh_few": {"ru": "Объявлений в расчёте {n} — меньше {min}: оценка ориентировочная.",
               "uz": "Hisobdagi eʼlonlar {n} ta — {min} tadan kam: baholash taxminiy.",
               "en": "{n} listings used — fewer than {min}: the valuation is indicative."},
    "mh_calibrated": {"ru": "Пороги (число объявлений, выбросы, срок, расхождение) — экспертные, не калибровано.",
                      "uz": "Chegaralar (eʼlonlar soni, chetlanishlar, muddat, farq) — ekspert, kalibrlanmagan.",
                      "en": "Thresholds (number of listings, outliers, age, difference) are expert values, not "
                            "calibrated."},
    "c_market_refine": {"ru": "Уточнить стоимость объекта: по объявлениям ({n} шт.) медиана {median}, вилка от {low} "
                              "до {high}; заявлено {declared} (расхождение {diff})",
                        "uz": "Obyekt qiymatini aniqlashtirish: eʼlonlar boʻyicha ({n} ta) mediana {median}, oraliq "
                              "{low} dan {high} gacha; koʻrsatilgan {declared} (farq {diff})",
                        "en": "Refine the object value: by listings ({n}) the median is {median}, range {low} to "
                              "{high}; declared {declared} (difference {diff})"},
    "c_market_edits": {"ru": "Проверить правки сотрудника в объявлениях (снято {unchecked}, включено {checked}, "
                             "исправлено цен {price_changed}, убрано {removed}, добавлено вручную {manual}): медиана "
                             "с правками {median}, без правок — по объявлениям, как их прочитала модель, — {orig}",
                       "uz": "Eʼlonlardagi xodim tuzatishlarini tekshirish (olib tashlangan {unchecked}, qoʻshilgan "
                             "{checked}, narxi tuzatilgan {price_changed}, roʻyxatdan olingan {removed}, qoʻlda "
                             "qoʻshilgan {manual}): tuzatishlar bilan mediana {median}, tuzatishlarsiz — model oʻqigan "
                             "eʼlonlar boʻyicha — {orig}",
                       "en": "Check the staff edits to the listings (unticked {unchecked}, ticked {checked}, prices "
                             "corrected {price_changed}, removed {removed}, added manually {manual}): the median with "
                             "the edits is {median}, without them — by the listings as the model read them — {orig}"},
    "c_market_under": {"ru": "Страховая сумма составляет {ratio} уточнённой стоимости — предупредить клиента о "
                             "пропорциональной выплате или довести сумму до стоимости ({ref})",
                       "uz": "Sugʻurta summasi aniqlashtirilgan qiymatning {ratio} qismi — mijozni mutanosib toʻlov "
                             "haqida ogohlantirish yoki summani qiymatgacha yetkazish ({ref})",
                       "en": "The sum insured is {ratio} of the refined value — warn the client about proportional "
                             "settlement or raise the sum to the value ({ref})"},
    "c_market_over": {"ru": "Страховая сумма выше уточнённой стоимости на {diff} — снизить сумму до стоимости ({ref})",
                      "uz": "Sugʻurta summasi aniqlashtirilgan qiymatdan {diff} ga yuqori — summani qiymatgacha "
                            "kamaytirish ({ref})",
                      "en": "The sum insured exceeds the refined value by {diff} — reduce the sum to the value ({ref})"},

    # ---------- /act/market/links и /act/market/shots ----------
    "mk_hint": {"ru": "Откройте ссылку в своём браузере, при необходимости уточните фильтры (год, регион) и сделайте "
                      "снимок экрана списка объявлений: должны быть видны названия, цены, годы и даты публикации — "
                      "5–10 объявлений. До {n} снимков за раз.",
                "uz": "Havolani oʻz brauzeringizda oching, kerak boʻlsa filtrlarni (yil, hudud) aniqlang va eʼlonlar "
                      "roʻyxatining skrinshotini oling: nomlari, narxlari, yillari va eʼlon sanalari koʻrinib tursin "
                      "— 5–10 ta eʼlon. Bir martada {n} tagacha surat.",
                "en": "Open the link in your own browser, refine the filters if needed (year, region) and take a "
                      "screenshot of the list of listings: titles, prices, years and publication dates must be "
                      "visible — 5–10 listings. Up to {n} screenshots at a time."},
    "mk_tip_price": {"ru": "цена и валюта каждого объявления", "uz": "har bir eʼlonning narxi va valyutasi",
                     "en": "the price and currency of each listing"},
    "mk_tip_year": {"ru": "год выпуска и пробег или моточасы, если указаны",
                    "uz": "ishlab chiqarilgan yili va yurgan masofasi yoki motosoati, agar koʻrsatilgan boʻlsa",
                    "en": "year of manufacture and mileage or engine hours, if shown"},
    "mk_tip_date": {"ru": "дата публикации («сегодня», «вчера» или число)",
                    "uz": "eʼlon sanasi («bugun», «kecha» yoki sana)",
                    "en": "publication date (\"today\", \"yesterday\" or a date)"},
    "mk_tip_pd": {"ru": "имена и телефоны продавцов не нужны — они не извлекаются",
                  "uz": "sotuvchilarning ismlari va telefonlari kerak emas — ular olinmaydi",
                  "en": "sellers' names and phone numbers are not needed — they are not extracted"},
    "mk_server_note": {"ru": "Сервер только составляет адреса поиска и сам по ним не ходит.",
                       "uz": "Server faqat qidiruv manzillarini tuzadi va ularga oʻzi murojaat qilmaydi.",
                       "en": "The server only builds the search addresses and never opens them itself."},
    "mk_links_none": {"ru": "Укажите марку и модель или вид объекта — искать не по чему",
                      "uz": "Marka va modelni yoki obyekt turini koʻrsating — qidirish uchun maʼlumot yoʻq",
                      "en": "Enter the make and model or the object type — there is nothing to search for"},
    "mk_l_olx": {"ru": "OLX — поиск «{q}»", "uz": "OLX — «{q}» qidiruvi", "en": "OLX — search \"{q}\""},
    "mk_l_olx_year": {"ru": "OLX — поиск с годом «{q}»", "uz": "OLX — yil bilan qidiruv «{q}»",
                      "en": "OLX — search with year \"{q}\""},
    "mk_l_avtoelon": {"ru": "Avtoelon.uz — {what}", "uz": "Avtoelon.uz — {what}", "en": "Avtoelon.uz — {what}"},
    "mk_l_uybor": {"ru": "Uybor.uz — недвижимость", "uz": "Uybor.uz — koʻchmas mulk", "en": "Uybor.uz — real estate"},
    "mk_l_joymee": {"ru": "Joymee.uz — объявления", "uz": "Joymee.uz — eʼlonlar", "en": "Joymee.uz — listings"},
    "mk_h_olx_all": {"ru": "поиск по всему OLX", "uz": "butun OLX boʻyicha qidiruv", "en": "search across all of OLX"},
    "mk_h_olx_section": {"ru": "поиск в разделе сайта «{section}»", "uz": "saytning «{section}» boʻlimida qidiruv",
                         "en": "search in the site section \"{section}\""},
    "mk_h_olx_year": {"ru": "год в запросе сужает выдачу: если объявлений нет — берите ссылку без года",
                      "uz": "soʻrovdagi yil natijani toraytiradi: eʼlon boʻlmasa — yilsiz havolani oling",
                      "en": "the year narrows the results: if there are no listings, use the link without the year"},
    "mk_h_avtoelon_section": {"ru": "раздел сайта для этого вида техники", "uz": "ushbu texnika turi uchun sayt boʻlimi",
                              "en": "the site section for this type of machinery"},
    "mk_h_avtoelon_all": {"ru": "общий раздел спецтехники: выберите вид техники на сайте",
                          "uz": "maxsus texnikaning umumiy boʻlimi: saytda texnika turini tanlang",
                          "en": "the general special machinery section: choose the machine type on the site"},
    "mk_h_avtoelon_car": {"ru": "раздел легковых по марке и модели", "uz": "marka va model boʻyicha yengil avtomobillar "
                                                                          "boʻlimi",
                          "en": "passenger car section by make and model"},
    "mk_h_uybor": {"ru": "выберите вид недвижимости и регион на сайте", "uz": "saytda koʻchmas mulk turi va hududini "
                                                                            "tanlang",
                   "en": "choose the property type and region on the site"},
    "mk_h_joymee": {"ru": "общий список объявлений: уточните вид объекта и регион на сайте",
                    "uz": "eʼlonlarning umumiy roʻyxati: saytda obyekt turi va hududini aniqlang",
                    "en": "general list of listings: refine the object type and region on the site"},
    "mk_sec_cars": {"ru": "Легковые автомобили", "uz": "Yengil avtomobillar", "en": "Passenger cars"},
    "mk_sec_property": {"ru": "Недвижимость", "uz": "Koʻchmas mulk", "en": "Real estate"},
    "mk_ok": {"ru": "Прочитано объявлений: {n}, из них того же изделия: {k}. Проверьте цены и отметки — модель может "
                    "ошибаться.",
              "uz": "Oʻqilgan eʼlonlar: {n}, shundan xuddi shu buyum: {k}. Narxlar va belgilarni tekshiring — model "
                    "xato qilishi mumkin.",
              "en": "Listings read: {n}, of which the same item: {k}. Check the prices and ticks — the model can make "
                    "mistakes."},
    "mk_empty": {"ru": "Модель не нашла на снимках объявлений с ценами. Проверьте, что на снимке виден список "
                       "объявлений, или введите объявления вручную.",
                 "uz": "Model suratlarda narxi bor eʼlonlarni topmadi. Suratda eʼlonlar roʻyxati koʻrinishini "
                       "tekshiring yoki eʼlonlarni qoʻlda kiriting.",
                 "en": "The model found no listings with prices on the screenshots. Make sure the list of listings "
                       "is visible, or enter the listings manually."},
    "mk_ai_off": {"ru": "Чтение снимков недоступно: {reason}. Снимки сохранены; объявления можно ввести вручную.",
                  "uz": "Suratlarni oʻqish imkonsiz: {reason}. Suratlar saqlandi; eʼlonlarni qoʻlda kiritish mumkin.",
                  "en": "Reading the screenshots is unavailable: {reason}. The screenshots are saved; listings can "
                        "be entered manually."},
    "mk_too_many": {"ru": "За один раз — не больше {n} снимков", "uz": "Bir martada {n} tadan koʻp surat emas",
                    "en": "No more than {n} screenshots at a time"},
    "mk_format": {"ru": "принимаются только снимки экрана JPG и PNG", "uz": "faqat JPG va PNG skrinshotlar qabul "
                                                                         "qilinadi",
                  "en": "only JPG and PNG screenshots are accepted"},
    "mk_need_rate": {"ru": "Часть цен в долларах, а курс не получен: укажите курс доллара — без него такие объявления в "
                           "расчёт не войдут.",
                     "uz": "Narxlarning bir qismi dollarda, kurs esa olinmadi: dollar kursini koʻrsating — usiz bunday "
                           "eʼlonlar hisobga kirmaydi.",
                     "en": "Some prices are in US dollars but no rate was obtained: enter the US dollar rate — "
                           "without it such listings will not be used."},
    "mk_fx_line": {"ru": "Курс: 1 доллар = {rate} на {date}, источник — {src}.",
                   "uz": "Kurs: 1 dollar = {rate}, {date} holatiga, manba — {src}.",
                   "en": "Rate: 1 US dollar = {rate} as of {date}, source — {src}."},
    "fx_src_cbu": {"ru": "ЦБ РУз (cbu.uz); источник курса подлежит подтверждению заказчиком",
                   "uz": "OʻzR MB (cbu.uz); kurs manbai buyurtmachi tomonidan tasdiqlanishi kerak",
                   "en": "Central Bank of Uzbekistan (cbu.uz); the rate source is subject to customer confirmation"},
    "fx_src_manual": {"ru": "ручной курс заказчика в настройках оценки", "uz": "baholash sozlamalaridagi buyurtmachining "
                                                                              "qoʻlda kiritilgan kursi",
                      "en": "the customer's manual rate in the valuation settings"},
    "fx_src_employee": {"ru": "введён сотрудником", "uz": "xodim kiritgan", "en": "entered by staff"},
    "fx_src_cbu_unverified": {"ru": "ЦБ РУз (cbu.uz) — курс сервер выдал ранее при чтении снимков, повторно не сверен; "
                                    "источник курса подлежит подтверждению заказчиком",
                              "uz": "OʻzR MB (cbu.uz) — kursni server suratlarni oʻqishda avval bergan, qayta "
                                    "tekshirilmagan; kurs manbai buyurtmachi tomonidan tasdiqlanishi kerak",
                              "en": "Central Bank of Uzbekistan (cbu.uz) — the rate was issued by the server earlier "
                                    "when reading the screenshots and was not re-checked; the rate source is subject "
                                    "to customer confirmation"},
    "mk_pd_dropped": {"ru": "Данные продавцов со снимков не сохраняются: убрано значений — {n}.",
                      "uz": "Suratlardagi sotuvchilar maʼlumotlari saqlanmaydi: olib tashlangan qiymatlar — {n}.",
                      "en": "Sellers' data from the screenshots is not stored: values removed — {n}."},
    "mk_warn": {"ru": "Снимайте только список объявлений: имена и телефоны продавцов не извлекаются; снимки хранятся "
                      "24 часа. Снимки уходят в языковую модель как картинки. Сервер тестовый.",
                "uz": "Faqat eʼlonlar roʻyxatini suratga oling: sotuvchilarning ismlari va telefonlari olinmaydi; "
                      "suratlar 24 soat saqlanadi. Suratlar til modeliga rasm sifatida yuboriladi. Server test "
                      "rejimida.",
                "en": "Capture only the list of listings: sellers' names and phone numbers are not extracted; "
                      "screenshots are kept for 24 hours. Screenshots are sent to the language model as pictures. "
                      "This is a test server."},
    "mk_date_assumed": {"ru": "дата не видна — принята дата снимка", "uz": "sana koʻrinmaydi — surat sanasi olindi",
                        "en": "date not visible — screenshot date used"},
    "mk_date_missing": {"ru": "дата публикации не видна — в расчёт не берётся",
                        "uz": "eʼlon sanasi koʻrinmaydi — hisobga olinmaydi",
                        "en": "publication date not visible — not used"},
    "mk_date_bad": {"ru": "дата публикации позже даты снимков — некорректна, в расчёт не берётся",
                    "uz": "eʼlon sanasi suratlar sanasidan keyin — notoʻgʻri, hisobga olinmaydi",
                    "en": "publication date is later than the screenshot date — invalid, not used"},
}

TX_NAPP = {
    # какая строка НАПП взята для комплексного продукта (поправка рынка вилки)
    "rf_mkt_pack_exact": {"ru": "рынок — пакет НАПП «{pack}»: тот же набор классов, что у продукта {code}; ставка и "
                                "убыточность пакета, а не отдельного класса",
                          "uz": "bozor — SHNMA «{pack}» paketi: {code} mahsuloti bilan bir xil klasslar toʻplami; "
                                "alohida klass emas, paket tarifi va zararliligi",
                          "en": "market — NAPP package “{pack}”: the same set of classes as product {code}; the "
                                "package rate and loss ratio, not a single class"},
    "rf_mkt_pack_nearest": {"ru": "рынок — пакет НАПП «{pack}»: ближайший к составу продукта {code} ({classes}) — "
                                  "пакета ровно с этими классами в отчёте нет",
                            "uz": "bozor — SHNMA «{pack}» paketi: {code} mahsuloti tarkibiga ({classes}) eng yaqini — "
                                  "hisobotda aynan shu klasslar paketi yoʻq",
                            "en": "market — NAPP package “{pack}”: the closest to product {code} ({classes}) — the "
                                  "report has no package with exactly these classes"},
    "rf_mkt_pack_class": {"ru": "пакета НАПП с классами продукта {code} ({classes}) в отчёте нет — рынок по строке "
                                "класса {cls}",
                          "uz": "hisobotda {code} mahsuloti klasslari ({classes}) bilan SHNMA paketi yoʻq — bozor "
                                "{cls}-klass qatori boʻyicha",
                          "en": "the NAPP report has no package with the classes of product {code} ({classes}) — "
                                "the market is the class {cls} row"},
    "rf_mkt_fy_switch": {"ru": "по последнему срезу {last}, за полный {year} год {fy} — взята оценка за полный год "
                               "(скачок убыточности за неполный год)",
                         "uz": "oxirgi kesim boʻyicha {last}, toʻliq {year} yil uchun {fy} — toʻliq yil bahosi olindi "
                               "(toʻliq boʻlmagan yildagi zararlilik sakrashi)",
                         "en": "{last} by the latest snapshot, {fy} for the full year {year} — the full-year estimate "
                               "is used (loss-ratio jump within an incomplete year)"},
    "rf_mkt_pack_fy": {"ru": "пакет за полный {year} год: ставка {rate}, убыточность {lr}",
                       "uz": "paket toʻliq {year} yil uchun: tarif {rate}, zararlilik {lr}",
                       "en": "the package for the full year {year}: rate {rate}, loss ratio {lr}"},
    "rf_mkt_class_rows": {"ru": "по одиночным строкам классов: {rows} (убыточность, срез {date}; ставка: {rates})",
                          "uz": "klasslarning alohida qatorlari boʻyicha: {rows} (zararlilik, {date} kesimi; tarif: "
                                "{rates})",
                          "en": "by the single class rows: {rows} (loss ratio, snapshot {date}; rate: {rates})"},
    "rf_mkt_class_row": {"ru": "класс {cls} — {v}", "uz": "{cls}-klass — {v}", "en": "class {cls} — {v}"},
    "rf_mkt_class_row_na": {"ru": "класс {cls} — нет строки", "uz": "{cls}-klass — qator yoʻq",
                            "en": "class {cls} — no row"},
    "an_m_class_label": {"ru": "Класс {cls} отдельно (НАПП)", "uz": "{cls}-klass alohida (SHNMA)",
                         "en": "Class {cls} alone (NAPP)"},
    "an_m_class_value": {"ru": "ставка {rate}; убыточность {lr}", "uz": "tarif {rate}; zararlilik {lr}",
                         "en": "rate {rate}; loss ratio {lr}"},
    "an_m_class_fy": {"ru": "; за {year} год: ставка {rate}, убыточность {lr}",
                      "uz": "; {year} yil uchun: tarif {rate}, zararlilik {lr}",
                      "en": "; for {year}: rate {rate}, loss ratio {lr}"},
    "rf_mkt_pack_sub": {"ru": "; подкласс {sub} в отчёте НАПП входит в свой класс",
                        "uz": "; {sub} kichik klassi SHNMA hisobotida oʻz klassiga kiradi",
                        "en": "; subclass {sub} is included in its class in the NAPP report"},
    "rf_src_napp_claims": {"ru": "НАПП — страховой отчёт, претензии и договоры по регионам (листы 3.5, 3.4), срез на "
                                 "{date}",
                           "uz": "SHNMA — sugʻurta hisoboti, hududlar boʻyicha daʼvolar va shartnomalar (3.5, 3.4 "
                                 "varaqlar), {date} holatiga kesim",
                           "en": "NAPP — insurance report, claims and contracts by region (sheets 3.5, 3.4), "
                                 "snapshot as of {date}"},
    # раздел 4: пакет продукта в строке рынка
    "an_m_pack_label": {"ru": "строка классов {pack}", "uz": "{pack} klasslar qatori", "en": "row for classes {pack}"},
    "an_m_prod_exact": {"ru": "Взят пакет НАПП «{pack}» — тот же набор классов, что у продукта {code}; ставка и "
                              "убыточность — по пакету, а не по отдельному классу.",
                        "uz": "SHNMA «{pack}» paketi olindi — {code} mahsuloti bilan bir xil klasslar toʻplami; tarif "
                              "va zararlilik — alohida klass emas, paket boʻyicha.",
                        "en": "The NAPP package “{pack}” is used — the same set of classes as product {code}; the "
                              "rate and loss ratio are for the package, not a single class."},
    "an_m_prod_nearest": {"ru": "Взят пакет НАПП «{pack}» — ближайший к составу продукта {code} ({classes}): пакета "
                                "ровно с этими классами в отчёте нет.",
                          "uz": "SHNMA «{pack}» paketi olindi — {code} mahsuloti tarkibiga ({classes}) eng yaqini: "
                                "hisobotda aynan shu klasslar paketi yoʻq.",
                          "en": "The NAPP package “{pack}” is used — the closest to product {code} ({classes}): the "
                                "report has no package with exactly these classes."},
    "an_m_prod_class": {"ru": "Пакета НАПП с классами продукта {code} ({classes}) в отчёте нет — взята строка "
                              "класса {cls}.",
                        "uz": "Hisobotda {code} mahsuloti klasslari ({classes}) bilan SHNMA paketi yoʻq — "
                              "{cls}-klass qatori olindi.",
                        "en": "The NAPP report has no package with the classes of product {code} ({classes}) — the "
                              "class {cls} row is used."},
    "an_m_prod_sub": {"ru": " Подкласс {sub} в отчёте НАПП входит в свой класс.",
                      "uz": " {sub} kichik klassi SHNMA hisobotida oʻz klassiga kiradi.",
                      "en": " Subclass {sub} is included in its class in the NAPP report."},
    # раздел 4: претензии и подразделения (НАПП)
    "an_np_rc_name": {"ru": "Претензии в регионе (НАПП)", "uz": "Hududdagi sugʻurta daʼvolari (SHNMA)",
                      "en": "Insurance claims in the region (NAPP)"},
    "an_np_rc_name_rep": {"ru": "Претензии по республике (НАПП)", "uz": "Respublika boʻyicha sugʻurta daʼvolari (SHNMA)",
                          "en": "Insurance claims, whole country (NAPP)"},
    "an_np_per1000": {"ru": "{f} на 1 000 договоров", "uz": "1 000 shartnomaga {f}", "en": "{f} per 1,000 contracts"},
    "an_np_rc_line": {"ru": "Претензии в регионе: {f} на 1 000 договоров, по республике {cf}; доля отказов {r} "
                            "(республика {cr}); средняя выплата {a} (республика {ca}); претензий {n}; источник НАПП, "
                            "срез {date} ({m} мес.)",
                      "uz": "Hududdagi daʼvolar: 1 000 shartnomaga {f}, respublika boʻyicha {cf}; rad etilganlar "
                            "ulushi {r} (respublika {cr}); oʻrtacha toʻlov {a} (respublika {ca}); daʼvolar {n}; manba "
                            "SHNMA, {date} holatiga kesim ({m} oy)",
                      "en": "Claims in the region: {f} per 1,000 contracts, country {cf}; refusals {r} (country "
                            "{cr}); average payment {a} (country {ca}); claims {n}; source NAPP, snapshot {date} "
                            "({m} months)"},
    "an_np_rc_rep_line": {"ru": "Претензии по республике: {cf} на 1 000 договоров; доля отказов {cr}; средняя выплата "
                                "{ca}; регион не задан — сравнения с регионом нет; источник НАПП, срез {date} ({m} мес.)",
                          "uz": "Respublika boʻyicha daʼvolar: 1 000 shartnomaga {cf}; rad etilganlar ulushi {cr}; "
                                "oʻrtacha toʻlov {ca}; hudud koʻrsatilmagan — taqqoslash yoʻq; manba SHNMA, {date} "
                                "holatiga kesim ({m} oy)",
                          "en": "Claims, whole country: {cf} per 1,000 contracts; refusals {cr}; average payment {ca}; "
                                "no region given — no comparison; source NAPP, snapshot {date} ({m} months)"},
    "an_np_cc_name": {"ru": "Претензии: рынок / INSON (НАПП)", "uz": "Daʼvolar: bozor / INSON (SHNMA)",
                      "en": "Claims: market / INSON (NAPP)"},
    "an_np_cc_value": {"ru": "INSON {cf} / рынок {mf} на 1 000 договоров",
                       "uz": "1 000 shartnomaga INSON {cf} / bozor {mf}",
                       "en": "INSON {cf} / market {mf} per 1,000 contracts"},
    "an_np_cc_line": {"ru": "Претензии: рынок {mn} ({mf} на 1 000 договоров, отказов {mr}, средняя выплата {ma}) / "
                            "INSON {cn} ({cf} на 1 000 договоров, отказов {cr}, средняя выплата {ca}); источник НАПП, "
                            "срез {date} ({m} мес.)",
                      "uz": "Daʼvolar: bozor {mn} (1 000 shartnomaga {mf}, rad etilgan {mr}, oʻrtacha toʻlov {ma}) / "
                            "INSON {cn} (1 000 shartnomaga {cf}, rad etilgan {cr}, oʻrtacha toʻlov {ca}); manba SHNMA, "
                            "{date} holatiga kesim ({m} oy)",
                      "en": "Claims: market {mn} ({mf} per 1,000 contracts, refusals {mr}, average payment {ma}) / "
                            "INSON {cn} ({cf} per 1,000 contracts, refusals {cr}, average payment {ca}); source NAPP, "
                            "snapshot {date} ({m} months)"},
    "an_np_br_name": {"ru": "Подразделения INSON в регионе (все вместе, по отчёту НАПП)",
                      "uz": "INSONning hududdagi boʻlinmalari (barchasi birga, SHNMA hisoboti boʻyicha)",
                      "en": "INSON subdivisions in the region (all together, per the NAPP report)"},
    "an_np_br_value": {"ru": "убыточность {lr} / по компании {clr}", "uz": "zararlilik {lr} / kompaniya boʻyicha {clr}",
                       "en": "loss ratio {lr} / company {clr}"},
    "an_np_br_line": {"ru": "Подразделения INSON в регионе (все вместе, по отчёту НАПП): убыточность {lr} против "
                            "среднего по компании {clr}; средняя премия на договор {ap} против {cap}; премии {p} млн "
                            "сум, договоров {n}; источник НАПП, срез {date}",
                      "uz": "INSONning hududdagi boʻlinmalari (barchasi birga, SHNMA hisoboti boʻyicha): zararlilik "
                            "{lr}, kompaniya boʻyicha oʻrtacha {clr}; shartnomaga oʻrtacha mukofot {ap}, kompaniyada "
                            "{cap}; mukofotlar {p} mln soʻm, shartnomalar {n}; manba SHNMA, {date} holatiga kesim",
                      "en": "INSON subdivisions in the region (all together, per the NAPP report): loss ratio {lr} "
                            "against the company average {clr}; average premium per contract {ap} against {cap}; "
                            "premiums {p} million UZS, contracts {n}; source NAPP, snapshot {date}"},
    "an_np_br_small": {"ru": "малая база: договоров {n}, меньше {min} — убыточность и средняя премия по региону "
                             "неустойчивы, вывод делать осторожно",
                       "uz": "kichik baza: shartnomalar {n}, {min} dan kam — hudud boʻyicha zararlilik va oʻrtacha "
                             "mukofot barqaror emas, xulosa ehtiyotkorlik bilan",
                       "en": "small base: {n} contracts, fewer than {min} — the regional loss ratio and average "
                             "premium are unstable, conclude with care"},
    "an_np_rc_ref": {"ru": "в поправку ставки не входит: претензии учитываются по месту головных офисов "
                           "страховщиков, {share} — город Ташкент",
                     "uz": "tarif tuzatishiga kirmaydi: daʼvolar sugʻurtachilarning bosh ofislari joylashgan joy "
                           "boʻyicha hisobga olinadi, {share} — Toshkent shahri",
                     "en": "not part of the rate adjustment: claims are recorded where insurers' head offices are, "
                           "{share} — Tashkent city"},
    "an_np_rc_ref_na": {"ru": "в поправку ставки не входит: претензии учитываются по месту головных офисов "
                              "страховщиков, почти все — город Ташкент",
                        "uz": "tarif tuzatishiga kirmaydi: daʼvolar sugʻurtachilarning bosh ofislari joylashgan joy "
                              "boʻyicha hisobga olinadi, deyarli barchasi — Toshkent shahri",
                        "en": "not part of the rate adjustment: claims are recorded where insurers' head offices "
                              "are, almost all — Tashkent city"},
    "an_np_rc_in_fork": {"ru": "входит в поправку ставки с весом {w} (включено в настройках); претензии учитываются "
                               "по месту головных офисов страховщиков — сравнение региона искажено",
                         "uz": "tarif tuzatishiga {w} vazn bilan kiradi (sozlamalarda yoqilgan); daʼvolar "
                               "sugʻurtachilarning bosh ofislari boʻyicha hisobga olinadi — hudud taqqoslashi buzilgan",
                         "en": "part of the rate adjustment with weight {w} (enabled in the settings); claims are "
                               "recorded where insurers' head offices are — the regional comparison is distorted"},
    "an_np_caveats": {"ru": "Оговорки: {text}", "uz": "Izohlar: {text}", "en": "Caveats: {text}"},
    "an_np_br_not_listed": {"ru": "в отчёте НАПП подразделения {company} не выделены",
                            "uz": "SHNMA hisobotida {company} boʻlinmalari alohida koʻrsatilmagan",
                            "en": "the NAPP report does not list {company}'s subdivisions"},
    "an_np_br_not_listed_reg": {"ru": "в отчёте НАПП подразделения {company} в регионе «{region}» не выделены (премий "
                                      "по региону нет)",
                                "uz": "SHNMA hisobotida {company}ning «{region}» hududidagi boʻlinmalari alohida "
                                      "koʻrsatilmagan (hudud boʻyicha mukofot yoʻq)",
                                "en": "the NAPP report does not list {company}'s subdivisions in “{region}” (no "
                                      "premiums in the region)"},
    "an_np_no_sheet": {"ru": "в отчёте НАПП за срез {date} листа {sheet} нет — показатель пропущен",
                       "uz": "SHNMA hisobotida {date} kesimi uchun {sheet} varagʻi yoʻq — koʻrsatkich oʻtkazib yuborildi",
                       "en": "the NAPP report for the {date} snapshot has no sheet {sheet} — the indicator is skipped"},
    "an_np_no_region_row": {"ru": "в листе 3.5 за срез {date} нет строки региона «{region}» — показатель пропущен",
                            "uz": "{date} kesimidagi 3.5 varaqda «{region}» hududi qatori yoʻq — koʻrsatkich oʻtkazib "
                                  "yuborildi",
                            "en": "sheet 3.5 for {date} has no row for “{region}” — the indicator is skipped"},
    "an_np_no_contracts": {"ru": "нет числа действующих договоров (лист 3.4) за срез {date} — частота не считается",
                           "uz": "{date} kesimi uchun amaldagi shartnomalar soni (3.4 varaq) yoʻq — chastota "
                                 "hisoblanmaydi",
                           "en": "no number of active contracts (sheet 3.4) for {date} — the frequency is not computed"},
    "an_np_no_company": {"ru": "в листе 2.10 за срез {date} нет строки INSON — показатель пропущен",
                         "uz": "{date} kesimidagi 2.10 varaqda INSON qatori yoʻq — koʻrsatkich oʻtkazib yuborildi",
                         "en": "sheet 2.10 for {date} has no INSON row — the indicator is skipped"},
    "an_np_skip_line": {"ru": "{name}: {why}", "uz": "{name}: {why}", "en": "{name}: {why}"},
    "an_np_src": {"ru": "Источник: НАПП — страховой отчёт, листы {sheets}{file}, срез на {date} — {url}",
                  "uz": "Manba: SHNMA — sugʻurta hisoboti, {sheets} varaqlar, {date} holatiga kesim — {url}",
                  "en": "Source: NAPP — insurance report, sheets {sheets}, snapshot as of {date} — {url}"},
    "an_np_src_title": {"ru": "НАПП — страховой отчёт, листы {sheets}", "uz": "SHNMA — sugʻurta hisoboti, {sheets} varaqlar",
                        "en": "NAPP — insurance report, sheets {sheets}"},
}

# справка биржи УзРТСБ в разделе 3 (02.10.2026, app/uzex.py, act_analytics.exchange_background)
TX_EXCHANGE = {
    "ex_label": {"ru": "Справка биржи УзРТСБ", "uz": "UzRTXB birja maʼlumotnomasi", "en": "UzRCE exchange reference"},
    "ex_item": {"ru": "{group} — медиана {price}/{unit} по {n} сделкам, последняя дата {date}",
                "uz": "{group} — mediana {price}/{unit}, {n} ta bitim, oxirgi sana {date}",
                "en": "{group} — median {price}/{unit} over {n} deals, last date {date}"},
    "ex_text": {"ru": "{items}; источник uzex.uz", "uz": "{items}; manba uzex.uz", "en": "{items}; source uzex.uz"},
    "ex_note": {"ru": "биржевые цены реальных сделок, для сверки стоимости запасов/грузов; стоимость объекта не меняет",
                "uz": "real bitimlarning birja narxlari, zaxira/yuk qiymatini solishtirish uchun; obyekt qiymatini "
                      "oʻzgartirmaydi",
                "en": "exchange prices of actual deals, to cross-check the value of stock/cargo; the object value "
                      "is unchanged"},
    "ex_src": {"ru": "Справка биржи: реестр сделок УзРТСБ за {days} дней — {url}",
               "uz": "Birja maʼlumotnomasi: UzRTXB bitimlar reyestri, {days} kun — {url}",
               "en": "Exchange reference: UzRCE deal register, {days} days — {url}"},
}
