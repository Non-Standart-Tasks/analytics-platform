# `system.warning.shown` — реестр кодов

Когда основной поиск (`scenario.search.*`) проходит, но мы вынуждены показать
пользователю ⚠️ / ℹ️ плашку рядом с выдачей или вместо неё — параллельно
эмитим событие телеметрии `system.warning.shown` со **стабильным кодом** в
поле `data`. Так дашборд может группировать по причине предупреждения, а не
по тексту (тексты меняются часто и не индексируются).

## Привязка к сценарию

Каждое `system.warning.shown` уносит тот же `request_id`, что и родительский
`scenario.completed` / `scenario.failed`. Это позволяет одним JOIN-ом получить
полный контекст «что искали, что показалось, какие плашки увидел пользователь»:

```sql
SELECT
    s.payload->'scenario' AS scenario,
    array_agg(w.data) AS warnings
FROM events s
LEFT JOIN events w
    ON w.type = 'system.warning.shown'
   AND w.request_id = s.request_id
WHERE s.type IN ('scenario.completed', 'scenario.failed')
  AND s.event_time > now() - interval '24 hours'
GROUP BY s.event_id, s.payload;
```

## Где код хранится в Python

Канонический источник — `backend/packages/avia-system/src/avia_system/system_warnings.py`.
Не вводите коды по месту вызова, всегда импортируйте константу — иначе аналитика
«дрейфует» от продакшна.

## Reference

### compliance.* — валидация запроса

| Код                                   | Когда возникает                                                                                                                                                                                            | Поле `scenario` (полезная нагрузка)                                  |
|---------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| `compliance.violation`                | LLM-валидатор вернул `complies=False` с произвольным `comment` — общий случай compliance-фейла, не подпадает под более конкретные коды ниже. Показывается как ⚠️.                                              | `{comment: "..."}` (первые 512 символов LLM-комментария)             |
| `compliance.country_substituted`      | Пользователь указал страну («Узбекистан»), системный промпт LLM подставил её главный город (Ташкент). Поиск идёт дальше, показывается как ℹ️ note над выдачей.                                                  | `{country, city, iata, side: "origin"\|"destination"}`               |
| `compliance.country_unknown`          | После IATA-резолва получили 2-буквенный код (= ISO country code, не город). Python safety-net обрубает поиск, чтобы не словить HTTP 400 `invalid-destination` от Travelpayouts. Показывается как ⚠️.       | `{bad_side, bad_value, origin_iata, destination_iata}`               |
| `compliance.adults_too_many`          | Пользователь запросил `adults > 9` — Travelpayouts/OTT API такое не принимает. Fail-fast с явным сообщением «OTT поддерживает максимум 9 пассажиров». Показывается как ⚠️.                                  | `{adults_requested: int}`                                            |
| `compliance.date_beyond_horizon`      | Все даты направления (туда или непустого обратно) дальше +365 дней от сегодня — лимит Travelpayouts (HTTP 400 «search depth should be less than one year»). Поиск не запускаем, ⚠️ «Дата … дальше чем на год вперёд — укажите более раннюю дату». MCP — `needs_clarification`. Если дальше только часть окна — см. `search.dates_beyond_horizon_dropped`. | `{bad_dates: [YYYY-MM-DD, ...], side: "to"\|"back", origin, destination}` |
| `compliance.date_in_past`             | Дата поездки уже прошла: год, указанный пользователем, — прошедший («03.10.2026» 22.10.2026), прошло всё окно, или вылет без года прошёл на днях, а «обратно» за ним, перенесённое на год вперёд, — дальше горизонта (`explicit_year=false`). Раньше такую дату молча искали на год позже. Поиск не запускаем, просьба указать будущую дату; в `scenario.completed` — `early_return_reason="past_dates"`, MCP — `needs_clarification`. Прошедшие даты окна, в котором есть и будущие, отбрасываются без warning. | `{bad_dates: [YYYY-MM-DD, ...], side: "to"\|"back", explicit_year: bool, origin, destination}` |
| `compliance.return_before_departure`  | Все даты «обратно» раньше дат «туда» (год указан явно: «Туда: 30.07.2026 / Обратно: 09.07.2026»). Раньше все пары вылет×возврат отсеивались молча и пользователь получал «не найдено» без причины. Поиск не запускаем, ⚠️ «Дата обратно … раньше даты вылета … — проверьте даты поездки». MCP — `needs_clarification`. | `{bad_dates: [YYYY-MM-DD, ...], to_dates: [YYYY-MM-DD, ...], origin, destination}` |
| `compliance.return_year_corrected`    | Опечатка в годе «обратно» у поездки через Новый год («28.12.2026 - 05.01.2026»): дата с годом вылета раньше вылета — ищем её годом позже (05.01.2027), если поездка тогда не длиннее 120 дней. Поиск идёт, ℹ️-заметка над выдачей «Дату обратно … искали как …» (без заголовка «Возможно, вам подойдут»). | `{typed_dates: [YYYY-MM-DD, ...], searched_dates: [YYYY-MM-DD, ...], origin, destination}` |
| `compliance.beyond_year_corrected`    | Опечатка в годе вылета: все даты вылета с годом из текста дальше +365 дней, а годом раньше ещё впереди («Владивосток-Пекин, 25/12/2027» 16.09.2026) — ищем годом раньше (25.12.2026); «обратно» с тем же годом за горизонтом сдвигается так же. Раньше год не учитывался и искали так же, но молча. Поиск идёт, ℹ️-заметка над выдачей «Дату … искали как …: дальше чем на год вперёд билеты не ищутся». | `{typed_dates: [YYYY-MM-DD, ...], searched_dates: [YYYY-MM-DD, ...], origin, destination}` |
| `compliance.departure_date_missing`   | Пользователь не указал дату вылета и LLM не смог её доинферить (`aviasales_json['to'] == []`). Без даты Travelpayouts искать не умеет.                                                                    | `{origin, destination}`                                              |
| `compliance.complex_segment_missing`  | Хотя бы один сегмент complex-маршрута не распарсился (типично — LLM не дал даты на промежуточный сегмент). Без всех сегментов нельзя строить общий запрос.                                                | `{segments_count: int, failed_indices: [int, ...]}`                  |

### search.* — нюансы результата

| Код                                 | Когда возникает                                                                                                                                                                                            | Поле `scenario`                                                  |
|-------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| `search.no_direct_flights`          | Прямых рейсов в исходную дату нет, расширили поиск на соседние даты. Соответствует плашке «Прямых рейсов по вашим критериям нет / Соседние даты добавлены в поиск».                                       | —                                                                |
| `search.no_direct_neighboring`      | Даже на соседних датах прямых не нашлось — показываем рейсы с пересадками. Соответствует плашке «Прямых рейсов на соседние даты не найдено ... только с пересадками».                                     | —                                                                |
| `search.time_interval_expanded_to`  | Пользователь просил «утром туда», в этом интервале нет рейсов — расширили на соседние интервалы суток. Соответствует плашке «В заданном временном интервале ничего не найдено. Показаны ближайшие ...».    | `{intervals: ["5-8", "8-12", ...]}`                              |
| `search.time_interval_expanded_back`| То же для обратного направления.                                                                                                                                                                          | `{intervals: [...]}`                                             |
| `search.filter_not_applied`         | Один из заявленных фильтров (например, `max_stops`, `dep_airport_back`) не применился из-за отсутствия результатов в комбинации с другими. Эмитим **по одному событию на каждый отброшенный фильтр** — так дашборд группирует по `filter_key`. | `{filter_key, filter_name_ru}`                                   |
| `search.partial_dates`              | На часть запрошенных дат рейсов не нашлось — показываем результаты только по «удачным» датам.                                                                                                              | `{fail_dates: [YYYY-MM-DD, ...], ok_dates: [YYYY-MM-DD, ...]}`   |
| `search.dates_beyond_horizon_dropped`| Часть дат окна дальше +365 дней — по ним не искали, по остальным ищем; ℹ️-заметка над выдачей «Даты … не искали: они дальше чем на год вперёд». Отдельно от `compliance.date_beyond_horizon`: пустая выдача тут — «рейсов нет» (MCP — `not_found`), а не «уточните дату». | `{bad_dates: [YYYY-MM-DD, ...], origin, destination}`            |

## Топ-запросы

**Распределение по причинам за сутки:**
```sql
SELECT data AS code, COUNT(*) AS shown
FROM events
WHERE type = 'system.warning.shown'
  AND event_time > now() - interval '24 hours'
GROUP BY data
ORDER BY shown DESC;
```

**Все плашки для конкретного запроса:**
```sql
SELECT data AS code, payload->'scenario' AS context, event_time
FROM events
WHERE type = 'system.warning.shown'
  AND request_id = '<UUID>'
ORDER BY event_time;
```

**Какие фильтры чаще всего «не выживают»:**
```sql
SELECT
    payload->'scenario'->>'filter_key' AS filter_key,
    COUNT(*) AS dropped
FROM events
WHERE type = 'system.warning.shown'
  AND data = 'search.filter_not_applied'
  AND event_time > now() - interval '7 days'
GROUP BY filter_key
ORDER BY dropped DESC;
```

## При изменении / добавлении кода

1. Добавить константу в `system_warnings.py` (а не строку по месту вызова).
2. Обновить строку в таблице выше.
3. Если код влияет на пользовательский UX (а не только на телеметрию) —
   обновить и `analytics-platform/grafana/dashboards/error-registry.json`
   (панель «Системные предупреждения по типам»).
