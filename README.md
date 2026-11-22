# da-analytics

Мульти-проектный приёмник телеметрии + дашборды в Grafana.

## Что это

```
digital-assistant  ─┐
другой_проект      ─┼─HTTP POST──▶  receiver (FastAPI) ──▶ Postgres ◀── Grafana
ещё_один_проект    ─┘                     │
                                       Traefik (HTTPS + публичные домены)
```

- **receiver** — FastAPI-приёмник, валидирует JWT, проверяет, что событие
  принадлежит проекту из токена, и кладёт в БД через batch-очередь.
- **Postgres** — единая таблица `events` (партиционирована по месяцам),
  один проект отличается от другого полем `service`.
- **Grafana** — дашборды для команды. Datasource провижится автоматически
  из `grafana/provisioning/datasources/analytics.yml`. Лёгкая (~50 MB RAM),
  SQL пишем в панелях.
- **Traefik** — HTTPS и Let's Encrypt.

## Multi-project: как устроено

Один приёмник принимает события от любого числа проектов. Разделение через **`service`** —
это поле уже размечает события в `telemetry-client`. На него завязаны:

1. **Реестр** — таблица [`projects`](receiver/migrations/001_init.sql) (`key`, `name`, `description`).
   Новый проект = одна INSERT-строка.
2. **Безопасность** — JWT каждого проекта содержит claim `project`. Приёмник проверяет:
   `event.service == jwt.project` — проект А не может отправить события за проект Б.
3. **Индексы** — все составные индексы начинаются с `(service, ...)`, чтобы запросы внутри
   проекта были быстрыми независимо от объёма других проектов.
4. **Per-project схемы** — таблица [`event_schemas`](receiver/migrations/001_init.sql)
   (`project_key`, `event_type`, `json_schema`). Когда пришлёшь схемы по проектам — заливаем туда,
   приёмник валидирует payload против них. До этого момента — только базовая валидация общих полей.
5. **Per-project view'ы** — для удобства Grafana можно завести
   `CREATE VIEW events_<project> AS SELECT * FROM events WHERE service = '<key>'`.
   Авто-создание планируется в `scripts/register_project.sh` (TODO).

### Добавление нового проекта

1. `INSERT INTO projects (key, name) VALUES ('chat_system', 'Чат-бот');`
2. Сгенерировать JWT с claim `{"project": "chat_system"}`, выдать команде проекта.
3. Команда проекта в своём `telemetry-client` ставит `service="chat_system"` и подключается.
4. (Опционально) Прислать JSON-схемы событий → заливаем в `event_schemas`.
5. (Опционально) Создать `events_chat_system` view для Grafana.

## Деплой (заглушка)

В `.env.example` есть плейсхолдеры — заполни перед запуском:

| Переменная | Что это | Пример |
|---|---|---|
| `ANALYTICS_DOMAIN` | Единый домен платформы | `analytics-platform.example.com` |
| `TELEMETRY_JWT_SECRET` | Секрет для подписи JWT всех проектов | случайные 64 hex |
| `POSTGRES_PASSWORD` | Пароль БД | случайные 32 символа |
| `LETSENCRYPT_EMAIL` | Email для Let's Encrypt | `ops@example.com` |

```bash
cp .env.example .env
# заполни .env
docker compose up -d
```

После старта (на одном домене разделение по путям):
- `https://${ANALYTICS_DOMAIN}/` — Grafana
- `https://${ANALYTICS_DOMAIN}/api/v1/events` — приёмник телеметрии

## Комментарии к баг-репортам AMA в Grafana

На дашборде **AVIA AMA Bug Reports** выберите проект `AMA_avia_project`
и репорт в списке «Репорт», либо нажмите заголовок в таблице открытых репортов.
Панель показывает переписку по времени, роль автора и число вложений.
Ссылка «Репорт в AMA» открывает оригинал с файлами.

Импорт включается отдельно для команды, которой разрешено читать обсуждения
AMA. Перед включением проверьте аудиторию **всего источника данных**
`analytics-postgres`, включая Grafana Explore и прямых читателей БД.
Тексты обычных комментариев будут доступны этой аудитории. Внутренние
заметки и содержимое файлов импорт не запрашивает.

1. Для существующей БД примените миграцию до обновления дашборда:

   ```bash
   docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < receiver/migrations/002_ama_report_discussions.sql
   ```

   В новой БД миграция выполняется при первом старте Postgres.
2. Вне Git подготовьте файл для отдельной обычной учётной записи AMA
   с доступом к комментариям: `{"username":"…","password":"…"}`.
   Ограничьте чтение файла и укажите путь в `AMA_REPORT_READER_FILE` в `.env`.
3. Запустите `docker compose --profile report-comments up -d --build report-comments`.
   Успешный цикл виден в `docker compose logs report-comments` по строке
   `Synced … AMA report threads`.

Первый проход загружает обсуждения открытых и закрытых репортов, следующие
начинаются через 10 минут после завершения предыдущего. При ошибке API
остаётся последняя полная копия. При успешном проходе удалённые репорты и
комментарии исчезают из копии.

## Подключение проекта

```bash
# 1. Зарегистрировать проект в реестре
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "INSERT INTO projects(key, name) VALUES ('avia_system', 'Поиск авиабилетов');"

# 2. Сгенерировать JWT (пример с PyJWT)
python -c "import jwt; print(jwt.encode({'project':'avia_system'}, '$TELEMETRY_JWT_SECRET', algorithm='HS256'))"
```

В проекте (`digital-assistant/.env`):
```
TELEMETRY_ENDPOINT=https://api.analytics.example.com/api/v1/events
TELEMETRY_JWT=<JWT из шага 2>
TELEMETRY_SERVICE=avia_system   # ровно как в projects.key
```

`telemetry-client` уже умеет принимать `endpoint` и `service` — отдельным PR
в digital-assistant поменять hardcoded ENDPOINT в [client.py:31](../backend/packages/telemetry-client/src/telemetry_client/client.py).

## Схема событий

Все события от `telemetry-client` имеют общие поля + type-специфичные подобъекты
(`chat_message`, `llm_tokens`, `crm_task`, `request`, `response`).
Общие — отдельные колонки, type-специфичные — JSONB. См. [001_init.sql](receiver/migrations/001_init.sql).

## TODO до прод-деплоя

- [ ] `pg_partman` для авто-создания партиций по месяцам
- [ ] Бэкапы Postgres (pg_dump → S3 по cron)
- [ ] `alembic` вместо raw-SQL
- [ ] Rate-limit на приёмник (Traefik middleware)
- [ ] `scripts/register_project.sh` — atomic: INSERT в projects + создать view + сгенерировать JWT
- [ ] Валидация payload против `event_schemas.json_schema` (когда пользователь пришлёт схемы)
- [ ] Дашборды Grafana, экспортированные в `dashboards/`
