-- ============================================================================
-- Multi-project схема.
--
-- Один приёмник принимает события от многих проектов (avia, chat, crm, ...).
-- Разделение — по полю `service` (так уже размечает события telemetry-client).
-- Все проекты живут в одной таблице events, чтобы:
--   - кросс-проектная аналитика работала из коробки,
--   - не плодить лишний DDL при появлении нового проекта.
-- При этом каждый проект:
--   - регистрируется в таблице projects,
--   - имеет свой JWT (claim `project` = projects.key),
--   - может объявить per-event-type JSON-схему в event_schemas (для валидации).
--
-- Партиционирование таблицы events — по received_at (по месяцам).
-- Если у одного проекта понадобится изоляция (отдельные диски/тюнинг) —
-- можно добавить sub-партицию LIST BY service. Пока избыточно.
-- ============================================================================

-- Logical БД для Metabase (отдельный owner с правами только на свою БД).
CREATE USER metabase WITH PASSWORD 'changeme-metabase-db-password';
CREATE DATABASE metabase OWNER metabase;

-- ============================================================================
-- Реестр проектов.
-- ============================================================================
CREATE TABLE projects (
    key          VARCHAR(64)  PRIMARY KEY,    -- совпадает со `service` в payload
    name         VARCHAR(128) NOT NULL,        -- человекочитаемое имя
    description  TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Стартовые проекты — расширять по мере появления.
INSERT INTO projects (key, name, description) VALUES
    ('avia_system', 'Поиск авиабилетов', 'Avia search и рекомендации');

-- ============================================================================
-- Per-project per-event-type схемы (для будущей валидации payload).
-- Заполняется, когда пользователь пришлёт схемы по своим проектам.
-- Если для пары (project_key, event_type) схемы нет — приёмник пропускает
-- событие без жёсткой валидации (только общие поля).
-- ============================================================================
CREATE TABLE event_schemas (
    project_key  VARCHAR(64)  NOT NULL REFERENCES projects(key) ON DELETE CASCADE,
    event_type   VARCHAR(64)  NOT NULL,
    json_schema  JSONB        NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_key, event_type)
);

-- ============================================================================
-- События. Партиционируется по received_at (месяц).
-- service ссылается на projects.key (валидация на уровне приложения,
-- т.к. в partitioned-таблице нельзя сделать FK, не включив колонку в ключ).
-- ============================================================================
CREATE TABLE events (
    id               BIGSERIAL,
    event_id         UUID         NOT NULL,
    service          VARCHAR(64)  NOT NULL,     -- = projects.key
    type             VARCHAR(64)  NOT NULL,
    system_env       VARCHAR(16),
    status           VARCHAR(16),
    request_id       VARCHAR(128),
    data             TEXT,
    payload          JSONB,
    client_timestamp TIMESTAMPTZ  NOT NULL,
    received_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, received_at)
) PARTITION BY RANGE (received_at);

-- Все ключевые срезы аналитики начинаются с (service, ...) — поэтому service
-- идёт первой колонкой во всех составных индексах.
CREATE INDEX events_service_received_at_idx        ON events (service, received_at DESC);
CREATE INDEX events_service_type_received_at_idx   ON events (service, type, received_at DESC);
CREATE INDEX events_service_status_received_at_idx ON events (service, status, received_at DESC)
    WHERE status IS NOT NULL;
CREATE INDEX events_request_id_idx                 ON events (request_id) WHERE request_id IS NOT NULL;
CREATE INDEX events_payload_gin_idx                ON events USING GIN (payload);

-- Стартовые партиции: текущий и следующий месяц.
-- Дальше — pg_partman или cron-скрипт авто-создания партиций (TODO в README).
CREATE TABLE events_y2026m05 PARTITION OF events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE events_y2026m06 PARTITION OF events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- Per-partition event_id index (идемпотентность ретраев клиента).
CREATE UNIQUE INDEX events_y2026m05_event_id_idx ON events_y2026m05 (event_id);
CREATE UNIQUE INDEX events_y2026m06_event_id_idx ON events_y2026m06 (event_id);

-- ============================================================================
-- View-обёртки: для каждого проекта при добавлении полезно сделать view,
-- чтобы Metabase-пользователи фильтровались автоматически. Пример:
--   CREATE VIEW events_avia AS SELECT * FROM events WHERE service = 'avia_system';
-- Создаются скриптом при добавлении проекта (см. scripts/register_project.sh, TODO).
-- ============================================================================
