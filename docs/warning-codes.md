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
| `compliance.same_origin_destination` | Город вылета совпал с городом назначения (Санкт-Петербург — Санкт-Петербург, Шереметьево — Внуково). Поиск не запускаем, ⚠️ с просьбой уточнить маршрут, MCP — `needs_clarification`. Раньше TravelPayouts отвечал 400 и сценарий падал. | `{origin_raw, destination_raw, origin_iata, destination_iata}` |
| `compliance.city_ambiguous`           | Городов с таким названием несколько («Кордова»), выбран самый популярный по Aviasales suggest, ℹ️ «городов X несколько — взяли Y». Выбор по курируемому списку (Сидней → SYD) не эмитится. Поиск идёт. | `{side, name, iata, candidates: [...], source: "suggest"}` |
| `compliance.city_nearby_airport`      | В названном месте нет аэропорта — искали ближайший (`kind=nearby`: Будва → Тиват), главный аэропорт острова или региона (`kind=region`: Хайнань → Хайкоу) или единственное место из suggest (`kind=suggest`: опечатка). ℹ️ над выдачей, поиск идёт. | `{side, name, iata, airport, kind}` |
| `compliance.city_unresolved`          | Место не удалось превратить в IATA-код. Поиск не запускаем, ⚠️ с причиной вместо «не найдено», MCP — `needs_clarification`. `reason`: `no_airport`, `ambiguous`, `not_found`, `lookup_failed`. | `{side, name, reason, candidates, rejected_iata}` |
| `compliance.adults_too_many`          | Пользователь запросил `adults > 9` — Travelpayouts/OTT API такое не принимает. Fail-fast с явным сообщением «OTT поддерживает максимум 9 пассажиров». Показывается как ⚠️.                                  | `{adults_requested: int}`                                            |
| `compliance.date_beyond_horizon`      | Все даты направления (туда или непустого обратно) дальше +365 дней от сегодня — лимит Travelpayouts (HTTP 400 «search depth should be less than one year»). Поиск не запускаем, ⚠️ «Дата … дальше чем на год вперёд — укажите более раннюю дату». MCP — `needs_clarification`. Если дальше только часть окна — см. `search.dates_beyond_horizon_dropped`. | `{bad_dates: [YYYY-MM-DD, ...], side: "to"\|"back", origin, destination}` |
| `compliance.date_in_past`             | Дата поездки уже прошла: год, указанный пользователем, — прошедший («03.10.2026» 22.10.2026), прошло всё окно, или вылет без года прошёл на днях, а «обратно» за ним, перенесённое на год вперёд, — дальше горизонта (`explicit_year=false`). Раньше такую дату молча искали на год позже. Поиск не запускаем, просьба указать будущую дату; в `scenario.completed` — `early_return_reason="past_dates"`, MCP — `needs_clarification`. Прошедшие даты окна, в котором есть и будущие, отбрасываются без warning. | `{bad_dates: [YYYY-MM-DD, ...], side: "to"\|"back", explicit_year: bool, origin, destination}` |
| `compliance.return_before_departure`  | Все даты «обратно» раньше дат «туда» (год указан явно: «Туда: 30.07.2026 / Обратно: 09.07.2026»). Раньше все пары вылет×возврат отсеивались молча и пользователь получал «не найдено» без причины. Поиск не запускаем, ⚠️ «Дата обратно … раньше даты вылета … — проверьте даты поездки». MCP — `needs_clarification`. | `{bad_dates: [YYYY-MM-DD, ...], to_dates: [YYYY-MM-DD, ...], origin, destination}` |
| `compliance.return_year_corrected`    | Опечатка в годе «обратно» у поездки через Новый год («28.12.2026 - 05.01.2026»): дата с годом вылета раньше вылета — ищем её годом позже (05.01.2027), если поездка тогда не длиннее 120 дней. Поиск идёт, ℹ️-заметка над выдачей «Дату обратно … искали как …» (без заголовка «Возможно, вам подойдут»). | `{typed_dates: [YYYY-MM-DD, ...], searched_dates: [YYYY-MM-DD, ...], origin, destination}` |
| `compliance.beyond_year_corrected`    | Опечатка в годе вылета: все даты вылета с годом из текста дальше +365 дней, а годом раньше ещё впереди («Владивосток-Пекин, 25/12/2027» 16.09.2026) — ищем годом раньше (25.12.2026); «обратно» с тем же годом за горизонтом сдвигается так же. Раньше год не учитывался и искали так же, но молча. Поиск идёт, ℹ️-заметка над выдачей «Дату … искали как …: дальше чем на год вперёд билеты не ищутся». | `{typed_dates: [YYYY-MM-DD, ...], searched_dates: [YYYY-MM-DD, ...], origin, destination}` |
| `compliance.past_year_corrected`      | Опечатка в годе вылета на стыке лет: все даты вылета с годом из текста прошли, а годом позже скоро («05.01.2026» в декабре 2026) — ищем годом позже. Поиск идёт, ℹ️-заметка над выдачей. | `{typed_dates: [YYYY-MM-DD, ...], searched_dates: [YYYY-MM-DD, ...], origin, destination}` |
| `compliance.child_ages_assumed`       | Дети в запросе есть, а возраст указан не у всех — ищем как детей 2–11 лет, ℹ️-заметка с просьбой указать возраст, если ребёнку меньше 2 или 12+. Поиск идёт. | `{}` |
| `compliance.infants_seated`           | Младенцев больше, чем взрослых: без места летит один младенец на взрослого, остальных ищем на отдельном месте по детскому тарифу, ℹ️-заметка. Раньше поиск был пустым (TP 400). | `{adults, infants_requested, seated}` |
| `compliance.departure_date_missing`   | Пользователь не указал дату вылета и LLM не смог её доинферить (`aviasales_json['to'] == []`). Без даты Travelpayouts искать не умеет.                                                                    | `{origin, destination}`                                              |
| `compliance.route_missing`            | В сообщении нет маршрута — LLM не извлёк город вылета и/или назначения (типично «спасибо», «а подешевле?»). Ответ «введите запрос заново», MCP — `needs_clarification`. | `{origin_raw, destination_raw}` |
| `compliance.complex_segment_missing`  | Хотя бы один сегмент complex-маршрута не распарсился (типично — LLM не дал даты на промежуточный сегмент). Без всех сегментов нельзя строить общий запрос.                                                | `{segments_count: int, failed_indices: [int, ...]}`                  |
| `compliance.complex_segment_dates_inferred` | LLM не дал даты промежуточных сегментов сложного маршрута, код подставил «дата первого сегмента + N дней». Поиск идёт. Рост частоты — сигнал править промпт. | `{segments_count, inferred_indices: [int, ...]}` |
| `compliance.complex_segment_origins_inferred` | LLM создал лишний сегмент «город X, прибытие» (это пункт назначения предыдущего сегмента), код его удалил. Поиск идёт. | `{segments_count_before, removed_indices: [int, ...]}` |
| `compliance.complex_route_no_segments` | Запрос сочли сложным маршрутом, а повторный разбор не дал ни одного сегмента — ищем как обычный маршрут. Над выдачей ничего не показывается. | `{first_is_complex: true, second_is_complex: bool\|null}` |
| `compliance.travelpayouts_unknown_location` | TravelPayouts вернул 400 invalid-arguments по конкретному IATA-коду (FRU и подобные): наш справочник код знает, а партнёрский API — нет. Повторы прекращаются. | `{location, body_snippet}` |
| `compliance.travelpayouts_runtime_failure` | TravelPayouts не отдал данные (таймаут, сеть, 5xx, исчерпаны повторы). Если AMA в этом же поиске вернула предложения — ответ только по AMA с ℹ️-плашкой. | `{error_type, error_message, ama_proposals_count}` |

### search.* — нюансы результата

| Код                                 | Когда возникает                                                                                                                                                                                            | Поле `scenario`                                                  |
|-------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| `search.no_direct_flights`          | Прямых рейсов в исходную дату нет, расширили поиск на соседние даты. Соответствует плашке «Прямых рейсов по вашим критериям нет / Соседние даты добавлены в поиск».                                       | —                                                                |
| `search.no_direct_neighboring`      | Даже на соседних датах прямых не нашлось — показываем рейсы с пересадками. Соответствует плашке «Прямых рейсов на соседние даты не найдено ... только с пересадками».                                     | —                                                                |
| `search.time_interval_expanded_to`  | Пользователь просил «утром туда», в этом интервале нет рейсов — расширили на соседние интервалы суток. Соответствует плашке «В заданном временном интервале ничего не найдено. Показаны ближайшие ...».    | `{intervals: ["5-8", "8-12", ...]}`                              |
| `search.time_interval_expanded_back`| То же для обратного направления.                                                                                                                                                                          | `{intervals: [...]}`                                             |
| `search.filter_not_applied`         | Один из заявленных фильтров (например, `max_stops`, `dep_airport_back`) не применился из-за отсутствия результатов в комбинации с другими. Эмитим **по одному событию на каждый отброшенный фильтр** — так дашборд группирует по `filter_key`. | `{filter_key, filter_name_ru}`                                   |
| `search.airline_filter_dropped`     | Фильтр по авиакомпании снят (или авиакомпанию не исключили), ℹ️-заметка. `reason`: `unknown`, `ambiguous`, `unknown_code` — название не распознано; `not_in_results` — распознано неуверенно, а среди найденных перевозчиков её нет. По `name` пополняется `airline_aliases.json`. | `{name, kind: "preferred"\|"blacklist", reason}` |
| `search.partial_dates`              | На часть запрошенных дат рейсов не нашлось — показываем результаты только по «удачным» датам.                                                                                                              | `{fail_dates: [YYYY-MM-DD, ...], ok_dates: [YYYY-MM-DD, ...]}`   |
| `search.code_filters_zeroed`        | Пул билетов был, но фильтры (время вылета, авиакомпании, лоукост, иностранные, ручная кладь, багаж) отсеяли всё, над выдачей названы фильтры. `jointly` — обнулило сочетание, а не один фильтр. | `{filters: [...], jointly: bool, counts: {...}}` |
| `search.trip_date_lost`             | Дата из текста запроса потерялась при разборе, разбор повторён по исходному тексту. `recovered=true` — дата вернулась, плашки нет; `false` — ℹ️ «дату не учли». | `{lost_dates: [...], recovered: bool}` |
| `search.date_window_capped`         | Широкое окно дат: пар вылет×возврат больше лимита, искали по выборке ключевых дат, ℹ️ с просьбой сузить диапазон. | `{origin, destination, pairs_before, pairs_after}` |
| `search.date_window_too_wide`       | Окно дат настолько широкое, что и выборка не уложится в 300 с. Поиск не запускаем, ℹ️ «сузьте даты». | `{origin, destination, pairs_before, threshold}` |
| `search.dates_beyond_horizon_dropped`| Часть дат окна дальше +365 дней — по ним не искали, по остальным ищем; ℹ️-заметка над выдачей «Даты … не искали: они дальше чем на год вперёд». Отдельно от `compliance.date_beyond_horizon`: пустая выдача тут — «рейсов нет» (MCP — `not_found`), а не «уточните дату». | `{bad_dates: [YYYY-MM-DD, ...], origin, destination}`            |
| `search.preferences_unavailable`    | Шаг разбора пожеланий не ответил или вернул некорректный JSON — ищем без пожеланий (авиакомпании, пересадки, время вылета по умолчанию), ℹ️-заметка. | `{origin, destination, preferences_type}` |
| `search.travel_class_substituted`   | Запрошенный класс поиском не поддерживается: комфорт ищем экономом, первый — бизнесом. ℹ️-заметка. | `{requested: "premium_economy"\|"first", searched: "Y"\|"C"}` |
| `search.fare_terms_unverified`      | Просили возврат или обмен, а у части билетов условия по данным поиска неизвестны. Билеты оставлены, ℹ️-заметка. | `{filter_key: "needs_ticket_return"\|"needs_exchange"}` |
| `search.aborted`                    | Поиск оборван снаружи: таймаут 300 с чата или MCP (у того же `request_id` есть `scenario.failed` ArqTimeout), вытеснение MCP-поиска, рестарт воркера. Событие говорит, где стоял поиск. Плашки нет. | `{stage, elapsed_ms, deadline_s, stage_elapsed_ms, timings_ms, tp_pairs_total, tp_pairs_done, tp_failed_searches_count, ama_answered, ama_retry_attempted, date_window_capped, llm_started, llm_open, llm_slow}` |

### llm.* и memory.* — служебные, без плашки

| Код                | Когда возникает | Поле `scenario` |
|--------------------|-----------------|-----------------|
| `llm.call_timeout` | Вызов LLM в шаге разбора запроса оборван по таймауту (`AVIA_LLM_CALL_TIMEOUT_S`, 30 с), шаг повторён; следующие 10 минут вызовы в процессе идут без таймаута. | `{step, timeout_s}` |
| `memory.near_oom`  | Память процесса бэкенда подошла к лимиту пода. Шлётся синхронно из `telemetry-client` (`emit_near_oom_sync`), не через `system_warnings.py`. | — |

## Подписи на дашбордах

Русские подписи кодов в панелях Grafana генерируются из этой таблицы скриптом
`scripts/warning_labels.py`: он переписывает `CASE` между маркерами
`/* warning-labels:<колонка> */` и `/* /warning-labels */` в SQL панелей. Код без
подписи показывается как есть. В подписях не используйте кавычки и обратный слэш.

| Код | Подпись |
|-----|---------|
| `compliance.violation` | Запрос не прошёл проверку |
| `compliance.country_substituted` | Страна заменена на главный город |
| `compliance.country_unknown` | Код страны вместо города, поиск не запущен |
| `compliance.same_origin_destination` | Город вылета и назначения совпадают |
| `compliance.city_ambiguous` | Город неоднозначен, взят самый популярный |
| `compliance.city_nearby_airport` | Нет аэропорта, искали ближайший |
| `compliance.city_unresolved` | Место не распознано, поиск не запущен |
| `compliance.adults_too_many` | Больше 9 взрослых |
| `compliance.date_beyond_horizon` | Дата дальше чем на год, поиск не запущен |
| `compliance.date_in_past` | Дата в прошлом, поиск не запущен |
| `compliance.return_before_departure` | Обратно раньше вылета, поиск не запущен |
| `compliance.return_year_corrected` | Исправлен год даты обратно |
| `compliance.beyond_year_corrected` | Исправлен год вылета (дальше горизонта) |
| `compliance.past_year_corrected` | Исправлен год вылета (прошедший) |
| `compliance.child_ages_assumed` | Возраст детей не указан, искали 2–11 лет |
| `compliance.infants_seated` | Младенцев больше взрослых, часть на отдельном месте |
| `compliance.departure_date_missing` | Дата вылета не указана |
| `compliance.route_missing` | Маршрут не распознан |
| `compliance.complex_segment_missing` | Сложный маршрут: сегмент не распознан |
| `compliance.complex_segment_dates_inferred` | Сложный маршрут: даты подставлены автоматически |
| `compliance.complex_segment_origins_inferred` | Сложный маршрут: лишний сегмент удалён |
| `compliance.complex_route_no_segments` | Сложный маршрут не разобран, искали как обычный |
| `compliance.travelpayouts_unknown_location` | TravelPayouts не принимает код аэропорта |
| `compliance.travelpayouts_runtime_failure` | Сбой TravelPayouts |
| `search.no_direct_flights` | Прямых на дату нет |
| `search.no_direct_neighboring` | Прямых нет и на соседних датах, показаны с пересадками |
| `search.time_interval_expanded_to` | Расширено время вылета туда |
| `search.time_interval_expanded_back` | Расширено время вылета обратно |
| `search.filter_not_applied` | Фильтр не применён (нет результатов) |
| `search.airline_filter_dropped` | Фильтр авиакомпании снят |
| `search.partial_dates` | Часть дат без рейсов |
| `search.code_filters_zeroed` | Фильтры отсеяли все билеты |
| `search.trip_date_lost` | Дата из запроса потерялась при разборе |
| `search.date_window_capped` | Широкое окно дат, искали выборку |
| `search.date_window_too_wide` | Слишком широкое окно дат, поиск не запущен |
| `search.dates_beyond_horizon_dropped` | Часть дат дальше года, не искали |
| `search.preferences_unavailable` | Пожелания не разобраны, искали без них |
| `search.travel_class_substituted` | Класс заменён на доступный |
| `search.fare_terms_unverified` | Условия возврата или обмена не проверены |
| `search.aborted` | Поиск оборван (таймаут или рестарт) |
| `llm.call_timeout` | Таймаут вызова LLM, шаг повторён |
| `memory.near_oom` | Память процесса у лимита |

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
2. Добавить строку в таблицу своего раздела и подпись в «Подписи на дашбордах».
3. Перегенерировать панели и сверить коды с бэкендом:

   ```bash
   python3 scripts/warning_labels.py
   python3 scripts/warning_labels.py --check --codes ../digital-assistant-2.0/backend/packages/avia-system/src/avia_system/system_warnings.py
   ```

   `--check` падает, если SQL панелей расходится с таблицей подписей или если у кода
   из `system_warnings.py` нет строки в реестре или подписи.
