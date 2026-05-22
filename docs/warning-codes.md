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
| `compliance.date_beyond_horizon`      | Дата вылета > +365 дней от сегодня — Travelpayouts отвечает HTTP 400. **Зарезервирован, валидация в коде ещё не реализована.**                                                                            | `{bad_dates: [YYYY-MM-DD, ...]}`                                     |
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
