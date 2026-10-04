# Observability

## RequestTrace

Постоянный технический trace не должен содержать raw message text.

Рекомендуемые поля:
- request_id;
- user_id pseudonymous/internal;
- workflow;
- skill_version;
- ruleset_version;
- provider;
- model;
- safety_status;
- fallback_used;
- retry_count;
- latency_ms;
- tokens_in;
- tokens_out;
- cost_usd;
- validation_status;
- user_feedback;
- created_at.

## Metrics

### Product
- workflow usage;
- copy rate;
- send rate;
- thumbs up/down;
- retry/rephrase rate.

### Quality
- schema retry rate;
- validation failure rate;
- safety intervention rate;
- relationship-rule violation rate.

### Runtime
- p50/p95 latency;
- provider errors;
- timeout rate;
- token usage;
- cost/request.

## Experiments

A/B tests должны назначать:
- skill_version;
- model config;
- generation settings;
- validator config.

Нельзя смешивать несколько неизвестных изменений в одном эксперименте без отдельного attribution strategy.

## Persistent service metrics (v0.6 MVP)

Privacy-safe telemetry is persisted in PostgreSQL so it survives restarts and can be queried
or exported for scaling analysis (DAU, interaction frequency, service errors, latency, tokens,
LLM cost). User feedback/copy/send metrics listed above are not implemented in this step.

### Runtime design

- `TraceEvent` (`app/observability/trace.py`) remains the runtime event abstraction; it now
  carries the internal `user_id` (set by `WorkflowEngine` from `AssistRequest.user_id`).
- `WorkflowEngine` depends on the `TraceSink` protocol (`record(event)`), not on SQLAlchemy.
- `RequestTraceSink` — in-memory sink, used in memory/test mode.
- `PostgresTraceSink` (`app/observability/postgres_sink.py`) — used when
  `RELATIONSHIP_BACKEND=postgres` (Docker/production):
  - stage events are buffered in memory per `request_id`;
  - on the terminal `request` event all rows of the request are written with a **single async
    multi-row INSERT** in a background task, not awaited by the request path;
  - storage errors are caught and logged as a warning (logger `svoi_pravila.observability`)
    with only `request_id`, row count and exception class name — never raised into the workflow;
  - buffers are bounded (64 events/request, 10 000 in-flight requests);
  - on shutdown the container drains pending writes before disposing the DB engine.

### Schema: `service_events` (append-only)

Row mapping is an explicit allow-list (`event_to_row`); any other metadata key is dropped.

| column | type | notes |
|---|---|---|
| id | bigint PK identity | |
| at | timestamptz not null | UTC event time |
| request_id | uuid not null | |
| user_id | varchar(64) null | internal id only |
| relationship_id | varchar(128) null | id only (generate events) |
| workflow | varchar(32) not null | soften / decode / help-say |
| stage | varchar(32) not null | stage name or `request` (terminal) |
| status | varchar(16) not null | stage: completed/failed; request: ok/error/blocked/failed |
| attempt | integer not null | |
| latency_ms | integer not null | stage or end-to-end request latency |
| provider / model | varchar(32) / varchar(64) null | generate events |
| input_tokens / output_tokens | integer null | generate events |
| cost_usd | numeric(12,6) null | generate events |
| provider_latency_ms, generation_ms | integer null | generate events |
| validation_ms | integer null | validate events |
| generate_attempts | integer null | request events |
| ruleset_version | integer null | generate events |
| self_check_status | varchar(16) null | present / missing / malformed |
| error_type | varchar(64) null | exception class name only |

Indexes: `ix_service_events_at_stage (at, stage)`, `ix_service_events_request_id (request_id)`.
Created by `scripts/init_db.py` (`create_all`, non-destructive: only missing tables are created).

### Never stored

Raw user message, generated message/model output, prompts, relationship rule text,
aliases/names, self-check evidence text and failure codes, credentials, Telegram `initData`,
Telegram username/first/last name, exception messages. Redis checkpoint behavior is unchanged.

### Definitions

Request-level metrics use **only terminal rows (`stage='request'`)**; stage events and
generate retries never inflate request counts. Dates are UTC; ranges inclusive.

- **DAU(day)** — distinct non-null `user_id` with ≥1 terminal request that day (any status).
  `user_days` = Σ daily DAU; `dau_avg` = `user_days / days`.
- **Requests per DAU** (requests/user/day) — `total_requests / user_days`.
- **Workflow frequency** — terminal requests by `workflow`.
- **Error rate** — `(error + failed) / total_requests`; `blocked_rate` reported separately.
- **Generate retry rate** — requests with `generate_attempts > 1` / requests with `≥ 1`.
- **Latency** — p50/p95/p99 of terminal `latency_ms`, nearest-rank (= SQL `percentile_disc`).
- **Provider errors/timeouts** — failed `generate` rows grouped by `error_type`.
- **Tokens/cost** — every `generate`+`completed` row is one real provider call. Totals and the
  provider/model breakdown **include retried attempts** (real spend). Per-request averages
  divide by terminal request count. `cost_per_dau = total_cost_usd / user_days`. Calls that
  raised before returning carry no tokens/cost. If no call in the range reported `cost_usd`
    (e.g. GigaChat currently returns none), `total_cost_usd`/`cost_per_dau` are `null`, not 0;
    `cost_reported_calls` shows how many calls carried a cost.

### Export

```bash
python scripts/export_metrics.py --from 2026-10-01 --to 2026-10-31 --kind summary --format json
python scripts/export_metrics.py --from 2026-10-01 --to 2026-10-31 --kind summary --format csv
python scripts/export_metrics.py --from 2026-10-01 --to 2026-10-31 --kind events --format csv --out events.csv
```

`DATABASE_URL` comes from settings or `--database-url`.

### Sample SQL

```sql
SELECT date_trunc('day', at AT TIME ZONE 'UTC') AS day,
       count(DISTINCT user_id) AS dau,
       count(*) AS requests,
       avg((status IN ('error','failed'))::int) AS error_rate,
       percentile_disc(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
       percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99
FROM service_events WHERE stage = 'request' GROUP BY 1 ORDER BY 1;

SELECT provider, model, count(*) AS calls, sum(input_tokens), sum(output_tokens), sum(cost_usd)
FROM service_events WHERE stage = 'generate' AND status = 'completed' GROUP BY 1, 2;
```

### Retention

Recommended 90 days of raw events
(`DELETE FROM service_events WHERE at < now() - interval '90 days'`), exporting daily
summaries first for longer history. Not automated in this MVP.

### Connecting BI later

Plain PostgreSQL table: Metabase, Grafana (PostgreSQL datasource), Superset etc. can connect
with a read-only role (`GRANT SELECT ON service_events TO metrics_reader;`) and reuse the SQL
above — no application changes.

### Performance

Per event the request path does only in-memory work (dict build + list append). One multi-row
INSERT (~6–12 rows) per request runs in the background on the existing async pool (typically
1–5 ms DB time, off the critical path). Expected user-visible overhead: < 1 ms; no extra LLM calls.

### Known limitations

- Buffered events are lost if the process crashes before the terminal event/insert completes.
- No automatic retention or partitioning yet.
- Memory mode does not persist events.
