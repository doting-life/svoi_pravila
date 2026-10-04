# Load tests (acceptance criterion #1)

Tool: **k6** run via Docker (`grafana/k6:latest`); no host install needed.
Payloads are synthetic. User ids use the `loadtest-` prefix, so these test rows can be told apart from real user data in `service_events`.
Workflow mix: soften 40% / help-say 40% / decode 20%. The load generator makes no LLM calls itself.

| File | Purpose |
|---|---|
| `lib.js` | shared request helper and staged constant-arrival-rate scenarios |
| `platform.js` | fake provider, 1/2/5/10 RPS × `DURATION` (default 60 s) |
| `stress.js` | fake provider, 1→5→10→15→20→30 RPS (+`EXTRA_LEVELS`, e.g. `50`) |
| `gigachat.js` | real GigaChat, `LEVELS` (default `0.2,0.5,1,2`) × `DURATION`; aborts if checks < 95% |
| `summarize.py` | per-level k6 metrics + `service_events` window stats (tokens, TPM, provider TPS, retries, errors) |
| `sample_stats.ps1` / `stats_window.py` | `docker stats` sampler and per-window CPU/RAM aggregation |

## Run (PowerShell, repo root, Compose stack up)

```powershell
# 1. Separate fake-provider app on the compose network (does not touch the real app)
docker run -d --name sp-loadtest-fake --network svoipravila_default --env-file .env -e LLM_PROVIDER=fake -e RELATIONSHIP_BACKEND=postgres -e DATABASE_URL=postgresql+asyncpg://svoi_pravila:svoi_pravila@postgres:5432/svoi_pravila -e CHECKPOINT_BACKEND=redis -e REDIS_URL=redis://redis:6379/0 -p 127.0.0.1:8001:8000 svoi-pravila-app:local

# 2. Optional resource sampler (stop by creating loadtests/results/stop.flag)
powershell -NoProfile -ExecutionPolicy Bypass -File loadtests\sample_stats.ps1 -Out loadtests\results\stress-stats.csv -StopFile loadtests\results\stop.flag

# 3. Platform / stress
docker run --rm --network svoipravila_default -v "${PWD}/loadtests:/scripts" -v "${PWD}/loadtests/results:/results" -e BASE_URL=http://sp-loadtest-fake:8000 -e DURATION=60 grafana/k6:latest run --quiet /scripts/platform.js
docker run --rm --network svoipravila_default -v "${PWD}/loadtests:/scripts" -v "${PWD}/loadtests/results:/results" -e BASE_URL=http://sp-loadtest-fake:8000 -e EXTRA_LEVELS=50 grafana/k6:latest run --quiet /scripts/stress.js

# 4. Real GigaChat (PAID - estimate first: calls ~= sum(rps*duration); ~880 tokens/call observed)
docker run --rm --network svoipravila_default -v "${PWD}/loadtests:/scripts" -v "${PWD}/loadtests/results:/results" -e BASE_URL=http://app:8000 -e LEVELS=0.2,0.5 -e USER_PREFIX=loadtest-gc-user- -e USERS=10 -e SUMMARY=/results/gigachat-a-summary.json grafana/k6:latest run --quiet /scripts/gigachat.js

# 5. Summaries (record UTC start/end of each run)
.\.venv\Scripts\python loadtests\summarize.py loadtests\results\platform-summary.json --db --from <startZ> --to <endZ> --user-prefix loadtest- --database-url postgresql+asyncpg://svoi_pravila:svoi_pravila@127.0.0.1:5432/svoi_pravila
.\.venv\Scripts\python loadtests\stats_window.py loadtests\results\stress-stats.csv <startZ> <endZ>

# 6. Cleanup
docker rm -f sp-loadtest-fake
```

## Definitions

- **RPS**: completed/attempted user HTTP requests per second.
- **LLM TPS (provider TPS)**: actual provider generate calls per second, counting retries (completed `generate` rows in `service_events`).
- **Input TPM** = sum(input_tokens) / test_minutes. **Output TPM** = sum(output_tokens) / test_minutes. **Total TPM** = (input + output) / test_minutes.
- Token throughput per second is always written as **tokens/sec**, never as "TPS".
- Retry calls count toward provider TPS and TPM.
