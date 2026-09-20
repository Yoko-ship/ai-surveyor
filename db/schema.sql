-- База ИИ-сюрвейера. SQLite; переносится на PostgreSQL без переписывания логики.
-- Справочники -> расчёт -> проверки -> журнал. Всё, что меняется со временем, версионируется.

PRAGMA foreign_keys = ON;

-- ============ СПРАВОЧНИКИ ============

-- Учётные группы для резерва незаработанной премии (Положение 1882, п. 10)
CREATE TABLE IF NOT EXISTS groups (
    code        TEXT PRIMARY KEY,           -- 1..4
    name        TEXT NOT NULL,
    rnp_method  TEXT NOT NULL,              -- как считается РНП
    rnp_share   REAL                        -- доля базы; NULL = по дням (pro rata)
);

-- Классы страхования (Закон о страховой деятельности)
CREATE TABLE IF NOT EXISTS classes (
    code        TEXT PRIMARY KEY,           -- '1'..'17', '13з', '16у'
    name        TEXT NOT NULL,
    group_code  TEXT NOT NULL REFERENCES groups(code),
    branch      TEXT NOT NULL,              -- 'общее' | 'жизнь'
    kind        TEXT NOT NULL               -- 'имущество' | 'ответственность' | 'финансы' | 'личное'
);

-- Страховые риски (опасности) по классам: то, что агент включает и выключает
CREATE TABLE IF NOT EXISTS perils (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    class_code      TEXT NOT NULL REFERENCES classes(code),
    is_catastrophic INTEGER NOT NULL DEFAULT 0,
    base_share      REAL,                   -- доля в нетто-ставке класса, 0..1
    note            TEXT
);

-- Версии тарифов: и компании, и регулятора. Старые расчёты сохраняют свою версию.
CREATE TABLE IF NOT EXISTS tariff_versions (
    id             INTEGER PRIMARY KEY,
    level          TEXT NOT NULL,           -- 'компания' | 'регулятор'
    name           TEXT NOT NULL,
    document_ref   TEXT,
    effective_from TEXT NOT NULL,
    effective_to   TEXT
);

-- Продукты тарифной политики
CREATE TABLE IF NOT EXISTS products (
    code            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    rate_text       TEXT,                   -- как записано в тарифной политике
    commission_text TEXT,
    commission_pct  REAL,
    pricing_mode    TEXT NOT NULL,          -- 'ставка' | 'программа' | 'по согласованию' | 'генеральный договор' | 'нормативный акт'
    is_general      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'действует',
    note            TEXT
);

-- Продукт может состоять из частей, каждая в своём классе (Положение 1882, п. 11)
CREATE TABLE IF NOT EXISTS product_classes (
    product_code TEXT NOT NULL REFERENCES products(code),
    class_code   TEXT NOT NULL REFERENCES classes(code),
    part_no      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (product_code, class_code)
);

-- Минимальные ставки: нижний порог расчёта
CREATE TABLE IF NOT EXISTS min_rates (
    id                INTEGER PRIMARY KEY,
    tariff_version_id INTEGER NOT NULL REFERENCES tariff_versions(id),
    product_code      TEXT REFERENCES products(code),
    class_code        TEXT REFERENCES classes(code),
    payer_type        TEXT,                 -- 'юр' | 'физ' | NULL для всех
    min_rate_pct      REAL NOT NULL
);

-- Коэффициенты андеррайтинга: фактор -> вариант -> множитель
CREATE TABLE IF NOT EXISTS coefficients (
    id          INTEGER PRIMARY KEY,
    factor_code TEXT NOT NULL,
    factor_name TEXT NOT NULL,
    class_code  TEXT REFERENCES classes(code),
    option_code TEXT NOT NULL,
    option_name TEXT NOT NULL,
    multiplier  REAL NOT NULL,
    calibrated  INTEGER NOT NULL DEFAULT 0, -- 0 = экспертное значение, не на своей статистике
    source      TEXT
);

-- Базовые нетто-ставки по классу и типу объекта
CREATE TABLE IF NOT EXISTS base_rates (
    id           INTEGER PRIMARY KEY,
    class_code   TEXT NOT NULL REFERENCES classes(code),
    object_type  TEXT NOT NULL,
    net_rate_pct REAL NOT NULL,
    frequency    REAL,                      -- частота страховых случаев q
    severity     REAL,                      -- средняя тяжесть k
    calibrated   INTEGER NOT NULL DEFAULT 0,
    source       TEXT
);

-- Состав нагрузки (без РПМ — компания его не ведёт)
CREATE TABLE IF NOT EXISTS load_components (
    code     TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    share    REAL NOT NULL                  -- доля в брутто-ставке
);

-- Чек-листы документов
CREATE TABLE IF NOT EXISTS checklists (
    id          INTEGER PRIMARY KEY,
    scope_type  TEXT NOT NULL,              -- 'всегда' | 'класс' | 'продукт' | 'тип_объекта'
    scope_code  TEXT,
    doc_name    TEXT NOT NULL,
    required    INTEGER NOT NULL DEFAULT 1,
    condition   TEXT                        -- когда запрашивать дополнительно
);

-- Правила проверки: то, что система не даст нарушить
CREATE TABLE IF NOT EXISTS rules (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    severity    TEXT NOT NULL,              -- 'стоп' | 'предупреждение' | 'подсказка'
    legal_ref   TEXT,
    description TEXT NOT NULL,
    -- LAWWATCH-01: если изменился акт, на который ссылается правило, робот ставит
    -- «требует пересмотра». Снимает пометку только человек (юрист) — см. app/lawwatch.py.
    review_status TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'требует пересмотра'
    review_reason TEXT,                         -- «изменён акт pol_1806, редакция 23.06.2025 → 11.10.2026»
    review_since  TEXT                          -- когда поставлена пометка
);

-- Финансовые показатели компании: для лимита 20% на один риск
CREATE TABLE IF NOT EXISTS company_financials (
    report_date  TEXT PRIMARY KEY,
    own_funds    REAL NOT NULL,
    reserves     REAL NOT NULL,
    source       TEXT
);

-- Предупредительные мероприятия: что предписать страхователю при определённом факторе риска.
-- Не путать с РПМ (резервом): резерв компания не ведёт, а предписания выдаёт.
CREATE TABLE IF NOT EXISTS preventive_measures (
    code         TEXT PRIMARY KEY,
    class_code   TEXT REFERENCES classes(code),
    trigger_kind TEXT NOT NULL,             -- 'factor' | 'peril' | 'sum_over'
    trigger_key  TEXT NOT NULL,             -- factor_code | peril_code | порог суммы
    trigger_val  TEXT,                      -- option_code (для factor) или NULL
    measure      TEXT NOT NULL,             -- что сделать
    why          TEXT NOT NULL,             -- зачем, простыми словами
    effect_option TEXT,                     -- какой вариант фактора получится после выполнения
    mandatory_over REAL,                    -- обязательно как условие договора при страховой сумме выше этой
    deadline_days INTEGER                   -- срок выполнения, если условие договора
);

-- Рыночная статистика НАПП: временной ряд по строкам листа «классы страхования».
-- Значения премий и выплат — нарастающим итогом с начала года (как в отчёте); обязательства — на дату.
CREATE TABLE IF NOT EXISTS market_stats (
    report_date   TEXT NOT NULL,            -- дата среза, ГГГГ-ММ-ДД
    row_key       TEXT NOT NULL,            -- нормализованный ключ строки: total, cls8, cls8_9 ...
    row_name      TEXT NOT NULL,            -- как в отчёте
    premiums_ytd  REAL,
    payouts_ytd   REAL,
    liabilities   REAL,
    source_file   TEXT,
    loaded_at     TEXT NOT NULL,
    PRIMARY KEY (report_date, row_key)
);

-- Отчётность по резервам (Положение 1882): на отчётную дату, по учётной группе или виду
CREATE TABLE IF NOT EXISTS reserve_reports (
    report_date  TEXT NOT NULL,
    scope_type   TEXT NOT NULL,             -- 'группа' | 'класс' | 'вид' | 'итого'
    scope_code   TEXT NOT NULL,             -- '1'..'4', код класса, 'ОСГО', 'итого'
    rnp          REAL DEFAULT 0,            -- резерв незаработанной премии
    rzu          REAL DEFAULT 0,            -- заявленные, неурегулированные
    rpnu         REAL DEFAULT 0,            -- произошедшие, незаявленные
    stab         REAL DEFAULT 0,            -- стабилизационные (ОСГО/ОСГОР/ОСГОП)
    cat_reserve  REAL DEFAULT 0,            -- резерв катастроф
    other        REAL DEFAULT 0,
    base_premium_12m REAL,                  -- базовая премия за 12 мес. (для нормы РПНУ ≥ 10%)
    source       TEXT,
    PRIMARY KEY (report_date, scope_type, scope_code)
);

-- Выделенные активы под резервы (1882, гл. III–IV)
CREATE TABLE IF NOT EXISTS allocated_assets (
    report_date  TEXT NOT NULL,
    category     TEXT NOT NULL,             -- 'госбумаги' | 'депозиты' | 'деньги' | 'ценные бумаги' | 'недвижимость' | 'прочее'
    amount       REAL NOT NULL,
    is_liquid    INTEGER NOT NULL DEFAULT 1,-- входит в норму «не менее 70%» (п. 38)
    PRIMARY KEY (report_date, category)
);

-- Показатели платёжеспособности (1806): для маржи и ёмкости
CREATE TABLE IF NOT EXISTS solvency_reports (
    report_date        TEXT PRIMARY KEY,
    own_funds          REAL NOT NULL,       -- итог раздела I пассива
    deductions         REAL DEFAULT 0,      -- вычеты по п. 14
    premiums_12m       REAL,                -- начисленные премии за 12 мес. (за вычетом возвратов)
    claims_36m         REAL,                -- начисленные возмещения за 36 мес.
    claims_36m_net     REAL,                -- то же за вычетом доли перестраховщиков
    min_capital        REAL,                -- минимальный уставный капитал по закону
    top5_liabilities   REAL,                -- обязательства по пяти крупнейшим рискам
    source             TEXT
);

-- ============ РАБОЧИЕ ДАННЫЕ ============

CREATE TABLE IF NOT EXISTS agents (
    id             INTEGER PRIMARY KEY,
    eais_id        TEXT UNIQUE,             -- ID из Единой системы
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,           -- 'физ' | 'юр' | 'банк'
    commission_pct REAL,
    trained_at     TEXT,                    -- курс не менее 12 часов
    monitored_at   TEXT,                    -- мониторинг раз в полгода
    status         TEXT NOT NULL DEFAULT 'активен'
);

CREATE TABLE IF NOT EXISTS requests (
    id            INTEGER PRIMARY KEY,
    external_no   TEXT,                     -- номер договора в учётной системе
    branch        TEXT,
    product_code  TEXT REFERENCES products(code),
    policyholder  TEXT,
    beneficiary   TEXT,
    agent_id      INTEGER REFERENCES agents(id),
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'новый'
);

CREATE TABLE IF NOT EXISTS objects (
    id           INTEGER PRIMARY KEY,
    request_id   INTEGER NOT NULL REFERENCES requests(id),
    object_type  TEXT NOT NULL,
    address      TEXT,
    region       TEXT,
    seismic_zone INTEGER,
    value_amount REAL,                      -- страховая стоимость
    sum_insured  REAL,
    franchise    REAL DEFAULT 0,
    attributes   TEXT                       -- JSON: материал, год, площадь, защита
);

CREATE TABLE IF NOT EXISTS object_perils (
    object_id  INTEGER NOT NULL REFERENCES objects(id),
    peril_code TEXT NOT NULL REFERENCES perils(code),
    included   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (object_id, peril_code)
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES requests(id),
    doc_name    TEXT NOT NULL,
    file_path   TEXT,
    received    INTEGER NOT NULL DEFAULT 0,
    extracted   TEXT                        -- JSON: что ИИ извлёк из файла
);

CREATE TABLE IF NOT EXISTS calculations (
    id                INTEGER PRIMARY KEY,
    request_id        INTEGER NOT NULL REFERENCES requests(id),
    object_id         INTEGER REFERENCES objects(id),
    tariff_version_id INTEGER REFERENCES tariff_versions(id),
    net_rate_pct      REAL,                 -- нетто
    risk_load_pct     REAL,                 -- рисковая надбавка
    cat_load_pct      REAL,                 -- катастрофическая надбавка
    gross_rate_pct    REAL,                 -- техническая брутто-ставка
    min_rate_pct      REAL,                 -- сработавший минимум
    applied_rate_pct  REAL,                 -- что поставил агент
    premium           REAL,
    pml_amount        REAL,
    verdict           TEXT,                 -- 'ок' | 'на утверждение' | 'отклонено'
    explanation       TEXT,                 -- JSON: вклад каждого коэффициента
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS check_results (
    id             INTEGER PRIMARY KEY,
    calculation_id INTEGER NOT NULL REFERENCES calculations(id),
    rule_code      TEXT NOT NULL REFERENCES rules(code),
    status         TEXT NOT NULL,           -- 'пройдено' | 'нарушено'
    detail         TEXT
);

CREATE TABLE IF NOT EXISTS recommendations (
    id             INTEGER PRIMARY KEY,
    calculation_id INTEGER NOT NULL REFERENCES calculations(id),
    kind           TEXT NOT NULL,           -- 'франшиза' | 'риск' | 'защита' | 'страховая сумма'
    text           TEXT NOT NULL,
    premium_delta  REAL                     -- на сколько изменится премия
);

CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    who      TEXT,
    action   TEXT NOT NULL,
    entity   TEXT,
    detail   TEXT
);

-- ============ ПОРТФЕЛЬНЫЙ АУДИТ ============
-- Загрузка выгрузки договоров из учётной системы и массовая проверка движком (app/portfolio.py)

CREATE TABLE IF NOT EXISTS portfolio_batches (
    id           INTEGER PRIMARY KEY,
    imported_at  TEXT NOT NULL,
    file_name    TEXT,
    rows_total   INTEGER NOT NULL DEFAULT 0,
    rows_ok      INTEGER NOT NULL DEFAULT 0,
    rows_warn    INTEGER NOT NULL DEFAULT 0,
    rows_stop    INTEGER NOT NULL DEFAULT 0,
    mapping      TEXT                        -- JSON: поле -> найденная колонка
);

CREATE TABLE IF NOT EXISTS portfolio_reviews (
    id                 INTEGER PRIMARY KEY,
    batch_id           INTEGER NOT NULL REFERENCES portfolio_batches(id),
    imported_at        TEXT NOT NULL,
    file_name          TEXT,
    external_no        TEXT,                 -- номер договора из выгрузки
    product_code       TEXT,
    policyholder       TEXT,
    branch             TEXT,
    sum_insured        REAL,
    value_amount       REAL,
    premium_file       REAL,                 -- премия по выгрузке
    premium_calc       REAL,                 -- премия по расчётной ставке
    applied_rate_pct   REAL,
    technical_rate_pct REAL,
    min_rate_pct       REAL,
    verdict            TEXT,                 -- 'ок' | 'на утверждение' | 'отклонено' | 'не проверен'
    violations         TEXT,                 -- JSON: [{rule, status, title}]
    row_json           TEXT                  -- JSON: исходная строка выгрузки
);

CREATE INDEX IF NOT EXISTS ix_portfolio_batch ON portfolio_reviews(batch_id);

-- ============ КАЛИБРОВКА ТАРИФОВ ============

-- Убытки по договорам: основа для burning cost и частоты (учебник CII M97).
-- Пока выгрузок компании нет, таблица пустая; синтетика — только в тестах на копии базы.
CREATE TABLE IF NOT EXISTS claims (
    id            INTEGER PRIMARY KEY,
    request_id    INTEGER REFERENCES requests(id),  -- договор (запрос), по которому убыток
    external_no   TEXT,                     -- номер убытка в учётной системе
    class_code    TEXT,                     -- класс страхования (если не задан — берётся из договора)
    object_type   TEXT,                     -- тип объекта (если не задан — из договора)
    event_date    TEXT,                     -- дата события, ГГГГ-ММ-ДД
    reported_date TEXT,                     -- дата заявления
    paid_date     TEXT,                     -- дата выплаты
    cause         TEXT,                     -- причина: пожар, кража, ...
    claimed       REAL,                     -- заявлено
    paid          REAL,                     -- выплачено
    status        TEXT NOT NULL DEFAULT 'заявлен', -- 'заявлен' | 'оплачен' | 'отказ'
    source        TEXT                      -- откуда запись: учётная система, ручной ввод
);

-- Прогоны калибровки: каждое предложение хранится целиком, применяется только через утверждение
CREATE TABLE IF NOT EXISTS calibration_runs (
    id          INTEGER PRIMARY KEY,
    run_at      TEXT NOT NULL,
    period_from TEXT NOT NULL,
    period_to   TEXT NOT NULL,
    contracts   INTEGER,                    -- договоров в выборке
    claims      INTEGER,                    -- убытков в выборке
    method      TEXT,                       -- как считали (burning cost + теория доверия)
    credibility REAL,                       -- доверие Z по всей выборке
    proposals   TEXT,                       -- JSON: сводка и список предложений
    status      TEXT NOT NULL DEFAULT 'предложено', -- 'предложено' | 'утверждено' | 'отклонено'
    approved_by TEXT,
    approved_at TEXT,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS ix_claims_request ON claims(request_id);
CREATE INDEX IF NOT EXISTS ix_claims_event ON claims(event_date);

-- ============ ПОЛЬЗОВАТЕЛИ И ДОСТУП (app/auth.py) ============

-- Учётные записи людей. Заявка -> сверка с реестром агентов (таблица agents, ID из ЕАИС по Положению 3845 п. 4)
-- -> подтверждение админом -> вход. Пароль хранится только как PBKDF2-HMAC-SHA256 с солью на пользователя.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    login         TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,
    phone         TEXT,
    role          TEXT NOT NULL DEFAULT 'агент',   -- 'агент' | 'андеррайтер' | 'актуарий' | 'админ'
    branch        TEXT,
    agent_eais_id TEXT,                            -- для роли «агент» обязателен: без ID в ЕАИС агент работать не может
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'ожидает подтверждения', -- | 'активен' | 'заблокирован'
    telegram_id   TEXT,                            -- если задан — при входе нужен код из Telegram
    position      TEXT,                            -- должность со слов самого человека (регистрация в боте)
    created_at    TEXT NOT NULL,
    approved_by   TEXT,                            -- логин админа, подтвердившего заявку
    approved_at   TEXT,
    last_login    TEXT
);

-- Сессии: случайный токен в httpOnly-cookie «sid», 12 часов, продлевается при активности
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT
);

-- Одноразовые коды второго шага входа (6 цифр, 5 минут)
CREATE TABLE IF NOT EXISTS login_codes (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    code       TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    channel    TEXT NOT NULL DEFAULT 'telegram',  -- 'telegram' | 'sms' | 'email'
    attempts   INTEGER NOT NULL DEFAULT 0         -- неверных попыток; после 5 код сгорает
);

CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_login_codes_user ON login_codes(user_id);

CREATE INDEX IF NOT EXISTS ix_requests_created ON requests(created_at);
CREATE INDEX IF NOT EXISTS ix_calc_request ON calculations(request_id);
CREATE INDEX IF NOT EXISTS ix_objects_request ON objects(request_id);
CREATE INDEX IF NOT EXISTS ix_perils_class ON perils(class_code);

-- ============ ДЕЛЕГИРОВАНИЕ И ОТЧЁТЫ ============

-- Задачи агентов: заказчик ставит задачу → руководитель разбивает и раздаёт → исполнители отчитываются
CREATE TABLE IF NOT EXISTS agent_tasks (
    id          INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES agent_tasks(id),   -- подзадача родительской
    created_at  TEXT NOT NULL,
    author      TEXT NOT NULL,                        -- кто поставил: 'заказчик' | код агента
    assignee    TEXT NOT NULL,                        -- код агента-исполнителя
    title       TEXT NOT NULL,
    detail      TEXT,
    status      TEXT NOT NULL DEFAULT 'новая',        -- 'новая' | 'в работе' | 'сделана' | 'ждёт заказчика' | 'отменена'
    result      TEXT,
    started_at  TEXT,
    done_at     TEXT
);

-- Ежедневные отчёты: собираются сервером в 08:00, хранятся как markdown
CREATE TABLE IF NOT EXISTS daily_reports (
    report_date TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    body_md     TEXT NOT NULL,
    stats       TEXT                                   -- JSON с цифрами, из которых собран текст
);

-- ============ ЗНАНИЯ КОМАНДЫ ============
-- Пробелы в знаниях и их закрытие. Перестрахование в продукт не входит (решение заказчика 20.09.2026),
-- но понятие о нём команда обязана иметь — поэтому оно живёт здесь как тема, а не как модуль расчёта.

CREATE TABLE IF NOT EXISTS knowledge_topics (
    id          INTEGER PRIMARY KEY,
    topic       TEXT NOT NULL UNIQUE,
    area        TEXT NOT NULL,                       -- 'закон'|'актуарий'|'рынок'|'перестрахование'|'андеррайтинг'|'такафул'|'ит'
    status      TEXT NOT NULL DEFAULT 'пробел',      -- 'пробел' | 'изучено'
    priority    INTEGER NOT NULL DEFAULT 5,          -- 1 — самый срочный
    source_hint TEXT,                                -- где искать: учебник, норма, сайт
    created_at  TEXT NOT NULL,
    learned_at  TEXT,
    note_path   TEXT                                 -- путь к заметке в docs/Знания
);

CREATE TABLE IF NOT EXISTS knowledge_log (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    agent    TEXT NOT NULL,                          -- код агента: law | actuary | data | backend | ui | reviewer | lead
    topic_id INTEGER REFERENCES knowledge_topics(id),
    what     TEXT NOT NULL,                          -- что именно понято
    source   TEXT,                                   -- откуда
    minutes  INTEGER                                 -- сколько времени заняло
);

CREATE INDEX IF NOT EXISTS ix_knowledge_topics_status ON knowledge_topics(status, priority);
CREATE INDEX IF NOT EXISTS ix_knowledge_log_ts ON knowledge_log(ts);

-- ============ ФОТОГРАФИИ ОБЪЕКТА ============
-- Файлы лежат в data/photos/<request_id>/, в базе только путь относительно корня проекта.
-- Имя файла генерируем сами: клиентскому имени не доверяем (оно хранится отдельно как filename).

CREATE TABLE IF NOT EXISTS photos (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES requests(id),
    path        TEXT NOT NULL,             -- относительный путь: data/photos/12/ab12cd.jpg
    filename    TEXT,                      -- как файл назывался у агента (только для показа)
    mime        TEXT NOT NULL,             -- image/jpeg | image/png | image/webp
    size_bytes  INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    uploaded_by TEXT,
    note        TEXT,
    -- вид файла: 'фото объекта' (по умолчанию) | 'техпаспорт' | 'кадастр'.
    -- PDF принимается только для документов, для фото объекта — нет.
    doc_kind    TEXT NOT NULL DEFAULT 'фото объекта',
    -- результат разбора документа: храним, чтобы не разбирать один и тот же файл дважды
    parse_status TEXT,                     -- 'разобран' | 'частично' | 'нужно распознавание' | 'ошибка'
    parsed_at    TEXT,
    parsed_json  TEXT                      -- JSON: поля, подписи, уверенность (без персональных данных)
);

CREATE INDEX IF NOT EXISTS ix_photos_request ON photos(request_id);
CREATE INDEX IF NOT EXISTS ix_photos_kind ON photos(request_id, doc_kind);

-- ============ ОЦЕНКА СТОИМОСТИ ОБЪЕКТА ============
-- Каркас: движок оценки пишет актуарий. Здесь только хранение норм, настроек и результатов.

-- Нормы амортизации (износа). Редактируются через админку.
-- rate_pct — процент износа за год; residual_min_pct — ниже какой доли от новой цены не опускаемся.
CREATE TABLE IF NOT EXISTS depreciation_norms (
    code              TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    rate_pct          REAL NOT NULL,
    residual_min_pct  REAL,
    calibrated        INTEGER NOT NULL DEFAULT 0,   -- 0 — экспертное значение, статистикой не подтверждено
    source            TEXT,
    note              TEXT
);

-- Настройки оценки: ключ — значение. Держим текстом, тип разбирает модуль (перенос на PostgreSQL без изменений).
CREATE TABLE IF NOT EXISTS valuation_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    name        TEXT NOT NULL,
    unit        TEXT,
    calibrated  INTEGER NOT NULL DEFAULT 0,
    source      TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS valuations (
    id                      INTEGER PRIMARY KEY,
    created_at              TEXT NOT NULL,
    created_by              TEXT,
    request_id              INTEGER REFERENCES requests(id),
    object_type             TEXT,
    params_json             TEXT,          -- JSON: что ввёл агент (год, пробег, площадь, состояние)
    declared_value          REAL,          -- заявленная страхователем стоимость
    ai_value                REAL,          -- что получилось у системы
    method                  TEXT,          -- как считали: 'объявления' | 'износ' | 'смешанный'
    method_version          TEXT,          -- версия методики — старые оценки должны воспроизводиться
    spread_json             TEXT,          -- JSON: вилка (минимум, медиана, максимум)
    explanation             TEXT,          -- JSON: из чего сложилась цифра
    confirmed_by_underwriter TEXT,
    confirmed_at            TEXT
);

-- По одной строке на источник данных. status обязателен: даже «нет данных» — это результат.
CREATE TABLE IF NOT EXISTS valuation_sources (
    id           INTEGER PRIMARY KEY,
    valuation_id INTEGER NOT NULL REFERENCES valuations(id),
    source       TEXT NOT NULL,
    status       TEXT NOT NULL,            -- 'ок' | 'нет данных' | 'источник недоступен' | 'запрещено robots.txt'
    url          TEXT,
    ads_count    INTEGER,
    median       REAL,
    q1           REAL,
    q3           REAL,
    samples_json TEXT,
    fetched_at   TEXT
);

CREATE INDEX IF NOT EXISTS ix_valuations_request ON valuations(request_id);
CREATE INDEX IF NOT EXISTS ix_valuation_sources_val ON valuation_sources(valuation_id);

-- ============ ОТКРЫТЫЕ ДАННЫЕ АГЕНТСТВА (КОМИТЕТА) СТАТИСТИКИ ============
-- Заполняется модулем app/stat_sources.py (реестр DATASETS).
-- Правила: каждая строка обязана нести ссылку на страницу набора (url);
-- история не перезаписывается — новые периоды добавляются, старые остаются.
-- Повторная загрузка того же периода не плодит дубли: первичный ключ
-- (source, dataset_id, key, region, period). Если значение изменилось
-- (статистика пересчитала ряд) — прежнее значение уходит в stat_series_revisions,
-- а в stat_series остаётся действующее. Так ряд всегда актуален, но видно,
-- что и когда пересчитали: расчёт прошлых лет можно воспроизвести.

CREATE TABLE IF NOT EXISTS stat_series (
    source      TEXT NOT NULL,             -- 'stat.uz' | 'data.egov.uz'
    dataset_id  TEXT NOT NULL,             -- наш ключ набора: 'population', 'housing_fund_area' ...
    key         TEXT NOT NULL,             -- ключ строки у источника (код СОАТО, код КИПЦ, вид ЧС)
    region      TEXT NOT NULL DEFAULT '',  -- 'region:ANDIJON' как в market_stats; 'total' = вся республика; '' = разреза нет
    period      TEXT NOT NULL,             -- '2025' | '2025-Q2' | '2026-M08'
    value       REAL,                      -- NULL = у источника нет значения (не придумываем)
    unit        TEXT,                      -- единица измерения как у источника
    fetched_at  TEXT NOT NULL,             -- когда мы забрали
    url         TEXT NOT NULL,             -- страница набора у источника (обязательна)
    PRIMARY KEY (source, dataset_id, key, region, period)
);

-- Журнал пересчётов: прежние значения тех же точек ряда.
CREATE TABLE IF NOT EXISTS stat_series_revisions (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    key         TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT '',
    period      TEXT NOT NULL,
    old_value   REAL,
    new_value   REAL,
    old_fetched_at TEXT,
    replaced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_stat_series_dataset ON stat_series(dataset_id, region, period);
CREATE INDEX IF NOT EXISTS ix_stat_series_period  ON stat_series(period);
CREATE INDEX IF NOT EXISTS ix_stat_revisions_key  ON stat_series_revisions(dataset_id, region, period);

-- ---------------------------------------------------------------------------
-- Коллективное согласование запросов (несколько человек на один запрос).
-- Поводом служит, в частности, генеральное соглашение с банком-партнёром.
-- ---------------------------------------------------------------------------

-- Генеральные соглашения с партнёрами (банк, лизинг и т. п.).
CREATE TABLE IF NOT EXISTS general_agreements (
    id           INTEGER PRIMARY KEY,
    partner      TEXT NOT NULL,                 -- «Asia Alliance Bank»
    product_code TEXT REFERENCES products(code),
    terms        TEXT,                          -- условия соглашения как есть, текстом
    default_reviewers TEXT,                     -- JSON-список user_id (или ФИО, если пользователя ещё нет)
    status       TEXT NOT NULL DEFAULT 'черновик', -- 'черновик' | 'действует' | 'расторгнуто'
    created_at   TEXT NOT NULL,
    note         TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_gen_agr_partner_product
    ON general_agreements(partner, COALESCE(product_code, ''));

-- Состав согласующих по запросу и их решения.
-- ФИО и должность копируются из users на момент назначения: состав решения
-- должен читаться спустя годы, даже если пользователя переименовали или уволили.
CREATE TABLE IF NOT EXISTS request_reviewers (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES requests(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    full_name   TEXT NOT NULL,
    position    TEXT,                           -- роль/должность на момент назначения
    order_no    INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'ожидает',-- 'ожидает' | 'одобрил' | 'отклонил' | 'вопрос'
    comment     TEXT,
    assigned_at TEXT NOT NULL,
    assigned_by TEXT,
    decided_at  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_request_reviewers ON request_reviewers(request_id, user_id);
CREATE INDEX IF NOT EXISTS ix_request_reviewers_user ON request_reviewers(user_id, status);
CREATE INDEX IF NOT EXISTS ix_request_reviewers_decided ON request_reviewers(decided_at);

-- ============ НАСТРОЙКИ, ИИ И ЗАПУСК НА СЕРВЕРЕ ============
-- Настройки приложения. Имеют приоритет над переменными окружения (.env): заказчик
-- меняет их на странице /admin/deploy, не заходя на сервер. Ключи наружу отдаются маской.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,               -- LLM_PROVIDER | LLM_BASE_URL | LLM_MODEL | LLM_API_KEY |
                                               -- TELEGRAM_BOT_TOKEN | PD_MODE | SERVER_URL
    value      TEXT,
    updated_at TEXT
);

-- Журнал обращений к ИИ. Содержимого запроса и персональных данных здесь нет —
-- только назначение вызова, метрики и текст ошибки (правило проекта № 8).
CREATE TABLE IF NOT EXISTS llm_calls (
    id                INTEGER PRIMARY KEY,
    ts                TEXT NOT NULL,
    purpose           TEXT NOT NULL,           -- 'объяснение расчёта' | 'поля документа' | 'проверка связи' | ...
    provider          TEXT,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    ms                INTEGER,
    ok                INTEGER NOT NULL DEFAULT 0,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS ix_llm_calls_ts ON llm_calls(ts);

-- Чек-лист готовности к промышленной эксплуатации (страница /admin/deploy)
CREATE TABLE IF NOT EXISTS deploy_checklist (
    key        TEXT PRIMARY KEY,               -- postgres | domain | https | backup | admin
    title      TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    note       TEXT,
    updated_at TEXT
);

-- ---------------------------------------------------------------------------
-- СЛЕЖЕНИЕ ЗА ЗАКОНОДАТЕЛЬСТВОМ (app/lawwatch.py)
-- Робот раз в сутки смотрит страницы актов на lex.uz и ленты новых документов
-- и новостей. Решение о том, что менять в движке, принимает человек — робот
-- только приносит событие. Исходные данные — docs/Отслеживаемые акты.json.
-- ---------------------------------------------------------------------------

-- Реестр отслеживаемых актов. Наполняется из docs/Отслеживаемые акты.json (tools/db_build.py).
CREATE TABLE IF NOT EXISTS watched_acts (
    code            TEXT PRIMARY KEY,          -- 'gk_ch52' | 'pol_1806' | ...
    title           TEXT NOT NULL,
    kind            TEXT,                      -- 'закон' | 'кодекс' | 'положение' | 'постановление' | 'внутренний акт'
    lex_url         TEXT,                      -- NULL, если адрес не установлен (ничего не выдумываем)
    redaction       TEXT,                      -- дата редакции из реестра, ДД.ММ.ГГГГ
    priority        TEXT,                      -- 'высокий' | 'средний' | 'низкий'
    why             TEXT,                      -- зачем следим: что от акта зависит
    rules_refs      TEXT,                      -- JSON-список кодов правил движка
    text_hash       TEXT,                      -- sha256 нормализованного текста страницы
    text_len        INTEGER,                   -- длина нормализованного текста (страницы-«оболочки» короткие)
    last_checked_at TEXT,
    last_changed_at TEXT,
    status          TEXT NOT NULL DEFAULT 'следим'
        -- 'следим' | 'изменился' | 'источник недоступен' | 'адрес не найден' | 'следит юрист'
);

CREATE INDEX IF NOT EXISTS ix_watched_acts_status ON watched_acts(status, priority);

-- Журнал событий: что робот увидел. seen=0 — юрист ещё не разбирал.
CREATE TABLE IF NOT EXISTS law_events (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL,                -- 'новая редакция' | 'новый документ' | 'новость'
    act_code     TEXT,                         -- пусто для новых документов и новостей
    title        TEXT NOT NULL,
    source       TEXT NOT NULL,                -- 'lex.uz' | 'napp.uz'
    url          TEXT,
    published_at TEXT,
    summary      TEXT,                         -- изложение системы, не текст акта
    seen         INTEGER NOT NULL DEFAULT 0,
    note         TEXT
);

CREATE INDEX IF NOT EXISTS ix_law_events_created ON law_events(created_at);
CREATE INDEX IF NOT EXISTS ix_law_events_seen ON law_events(seen, id);
CREATE INDEX IF NOT EXISTS ix_law_events_act ON law_events(act_code, id);

-- ---------------------------------------------------------------------------
-- Телеграм-бот (app/tgbot.py): приём обновлений, регистрация, согласование.
-- ПЕРСОНАЛЬНЫХ ДАННЫХ В ЭТИХ ТАБЛИЦАХ НЕТ (правило проекта № 8):
-- ни текстов сообщений, ни ФИО, ни телефонов — только идентификаторы и тип события.
-- ---------------------------------------------------------------------------

-- Журнал обмена с ботом: только «кто, куда, какого типа событие и получилось ли».
CREATE TABLE IF NOT EXISTS tg_messages (
    id          INTEGER PRIMARY KEY,
    update_id   INTEGER,                      -- номер обновления Telegram (для входящих)
    telegram_id TEXT,                         -- идентификатор чата/пользователя в Telegram
    user_id     INTEGER,                      -- наш пользователь, если он уже известен
    direction   TEXT NOT NULL,                -- 'in' | 'out'
    kind        TEXT NOT NULL,                -- 'start' | 'команда' | 'callback' | 'уведомление' | 'отказ' | 'ошибка'
    ok          INTEGER NOT NULL DEFAULT 1,
    error       TEXT,                         -- техническая причина (без текста сообщения)
    request_id  INTEGER,                      -- по какому запросу событие, если применимо
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tg_messages_created ON tg_messages(created_at);
CREATE INDEX IF NOT EXISTS ix_tg_messages_tg ON tg_messages(telegram_id, id);

-- Обработанные обновления: Telegram повторяет доставку, второй раз обрабатывать нельзя.
CREATE TABLE IF NOT EXISTS tg_updates (
    update_id   INTEGER PRIMARY KEY,
    received_at TEXT NOT NULL
);

-- Состояние пошагового диалога регистрации. Хранится в базе, а не в памяти процесса:
-- перезапуск сервера не теряет начатую заявку.
CREATE TABLE IF NOT EXISTS tg_dialogs (
    telegram_id TEXT PRIMARY KEY,
    step        TEXT NOT NULL,                -- 'согласие' | 'фио' | 'филиал' | 'должность' | 'роль' | 'id агента' | 'вопрос'
    draft       TEXT,                         -- JSON-черновик заявки; удаляется вместе со строкой после завершения
    updated_at  TEXT NOT NULL
);

-- Согласия на обработку персональных данных: факт, версия текста, канал и дата.
-- Самого текста согласия и данных человека здесь нет — только ссылка на версию.
CREATE TABLE IF NOT EXISTS pd_consents (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER,                      -- заполняется, когда заявка создана
    telegram_id TEXT NOT NULL,
    version     TEXT NOT NULL,                -- consent_version у юриста: 'ПД-1', 'ПД-2', ...
    channel     TEXT NOT NULL DEFAULT 'telegram',
    created_at  TEXT NOT NULL,                -- given_at у юриста: момент согласия
    -- раздел 7 docs/Регистрация и роли.md: доказательство, что текст не подменили задним числом
    consent_text_hash TEXT,
    scope       TEXT NOT NULL DEFAULT 'основное',   -- 'основное' | 'телефон' (телефон — отдельная строка)
    revoked_at  TEXT                          -- отзыв согласия: строки не удаляем, ставим дату (ст. 17 и 21 ЗРУ-547)
);

CREATE INDEX IF NOT EXISTS ix_pd_consents_tg ON pd_consents(telegram_id, id);
