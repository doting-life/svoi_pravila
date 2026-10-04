# Load Test Report — Acceptance Criterion #1

Criterion: *"Test-load report up to 10 RPS per service, TPM, TPS, stress tests."*
Date: 2026-10-04 (UTC). Tooling and commands: [loadtests/README.md](../loadtests/README.md). Raw k6 summaries are in `loadtests/results/`.
Sources: k6 client-side metrics, plus the persistent `service_events` table (filtered by `loadtest-` user prefix and UTC window).

## 1. Test environment

| Item | Value |
|---|---|
| Machine | Intel Core Ultra 7 258V, 8 cores / 8 threads, 31.5 GB RAM (laptop) |
| OS | Windows 11 Pro; Docker Desktop (8 vCPU / ~16.5 GB to Docker VM) |
| Docker / Compose | 29.8.1 / v5.5.1 |
| App | svoi-pravila 0.6.0, single uvicorn process (`svoi-pravila-app:local`) |
| Provider/model | Platform: `fake/fake-structured-v1`. Real: `gigachat/GigaChat-2-Pro:2.0.30.01` |
| PostgreSQL / Redis | 17.11 / 7.4.11 (Compose services, shared by both app instances) |
| Load generator | k6 (`grafana/k6:latest`) in Docker on the same Compose network (no tunnel) |

## 2. Platform test (fake provider, 60 s per level, 2026-10-04 20:41:36–20:45:58Z)

| RPS target | achieved | requests | error % | p50 | p90 | p95 | p99 | max | app CPU avg/max* | app RAM* |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.0 | 61 | 0 | 29 ms | 37 ms | 39 ms | 60 ms | 78 ms | 2% / 3% | 69 MiB |
| 2 | 2.0 | 120 | 0 | 22 ms | 31 ms | 33 ms | 37 ms | 46 ms | — | ~69 MiB |
| 5 | 5.0 | 301 | 0 | 22 ms | 29 ms | 34 ms | 39 ms | 43 ms | 13% / 16% | 69 MiB |
| 10 | 10.0 | 601 | 0 | 23 ms | 28 ms | 31 ms | 42 ms | 48 ms | 24% / 33% | 69 MiB |

\*The resource sampler did not start during the platform run. The CPU and RAM figures come from the same levels in the stress run (section 4), which used the same container and workload. CPU is a percentage of one vCPU.

`service_events` for the window: 1083 requests, 50 test users, 100% `ok`. Workflows: soften 443, help-say 424, decode 216. Server-side latency: p50 19, p95 28, p99 34 ms. No retries. `self_check_status=present` for 100%. PostgreSQL: `accepting connections`, 12 backend connections. Redis: `PONG`.

## 3. Real GigaChat end-to-end

Estimate before the run: 0.2 + 0.5 RPS × 60 s ≈ 42 calls × ~900–1700 tokens ≈ 40–70k tokens. Window: 20:52:03–20:53:06Z.

| RPS target | achieved | requests | error % | p50 | p95 | p99 | provider p95 | input TPM | output TPM | total TPM | provider TPS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.2 | 0.19 | 12 | **8.3** (1×HTTP 429) | 2.29 s | 9.57 s | 9.57 s | 9.55 s | 8,343 | 1,728 | 10,071 | 0.17 |
| 0.5 | not run | – | – | – | – | – | – | – | – | – | – |
| 1, 2, 5, 10 | not run | – | – | – | – | – | – | – | – | – | – |

- Provider latency was p50 2.42 s and p90 3.09 s, with an outlier at 9.55 s.
- Tokens per request averaged 730 input and 151 output.
- Successful calls had no retries and no ReadTimeouts.
- `self_check_status=present` for all 11 successful calls.
- Cost was not reported by GigaChat (`cost_usd = NULL`).
- **Stop reason:** `GigaChat chat completion failed with HTTP 429` (provider rate limiting) at 0.2 RPS. The error rate went above 5%, so k6's `abortOnFail` threshold ended the ramp. Under the safety rules, no higher level was attempted.

## 4. Stress test (fake provider, 2026-10-04 20:47:05–20:51:36Z)

| RPS | dur | requests | error % | p50 | p95 | p99 | max | app CPU avg/max | PG CPU avg | Redis CPU avg | app/PG/Redis RAM max |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 30 s | 30 | 0 | 20 ms | 30 ms | 48 ms | 54 ms | 2% / 3% | 2% | 3% | 69/36/11 MiB |
| 5 | 30 s | 151 | 0 | 24 ms | 39 ms | 50 ms | 54 ms | 13% / 16% | 5% | 4% | 69/37/11 MiB |
| 10 | 60 s | 601 | 0 | 22 ms | 38 ms | 47 ms | 75 ms | 24% / 33% | 6% | 5% | 69/38/14 MiB |
| 15 | 30 s | 451 | 0 | 21 ms | 33 ms | 47 ms | 61 ms | 33% / 40% | 7% | 7% | 69/40/20 MiB |
| 20 | 30 s | 601 | 0 | 19 ms | 31 ms | 41 ms | 68 ms | 41% / 46% | 8% | 7% | 70/42/18 MiB |
| 30 | 30 s | 901 | 0 | 17 ms | 37 ms | 66 ms | 110 ms | 51% / 60% | 11% | 8% | 71/43/22 MiB |
| 50 | 30 s | 1501 | 0 | 12 ms | 48 ms | **171 ms** | 250 ms | 56% / 72% | 11% | 8% | 73/50/28 MiB |

`service_events`: 4226 requests, all `ok`, 15.6 provider calls/s on average over the window. No breaking point was reached up to 50 RPS.
- **First sharp tail-latency increase:** 30 RPS for p99 (66 ms), then 50 RPS (p99 171 ms, p95 48 ms).
- **First non-zero error rate:** not reached (0% up to 50 RPS).
- **First resource pressure:** the single-process app container (CPU rises linearly, about 2.4% per RPS). PostgreSQL and Redis stayed at or below 11% CPU.

## 5. Bottleneck analysis

1. **External provider (dominant).** GigaChat rate-limits (HTTP 429) at about 0.2 RPS with the current account or quota, and it adds 2–10 s of latency. This is the binding constraint by orders of magnitude.
2. **App process.** With a fake provider, the only resource that grows noticeably is the single uvicorn worker's CPU. Linear extrapolation suggests saturation of one core around 100+ RPS. This was not tested.
3. **PostgreSQL / Redis.** These were not bottlenecks: ≤11% CPU, ≤50 MiB RAM, and healthy throughout.

## 6. SLA (1.5 s target)

| Path | p50 | p95 | Verdict |
|---|---|---|---|
| Platform (fake provider), up to 10 RPS (and up to 50) | 22 ms | 31–48 ms | **Met at p50 and p95** |
| Real GigaChat, 0.2 RPS | 2.29 s | 9.57 s | **Not met** (neither p50 nor p95) |

Provider latency (p50 2.42 s) by itself is already above 1.5 s. The platform overhead is about 20–30 ms.

## 7. Known limitations

- The real-provider sample is small (12 requests, 1 minute). Its p95/p99 values are effectively the maximum of the sample.
- GigaChat quota and rate limits depend on the account tier. A higher tier or a concurrency limit raise from the provider would change the result.
- The fake provider's token counts (~8 in / ~95 out) are not representative of real prompts (~730 in / ~150 out).
- Tests ran on a local laptop. The generator and system under test share the same Docker VM, and there was no TLS or tunnel, so production overhead is not included.
- The app ran as a single uvicorn process. The platform runs share PostgreSQL and Redis with the real app; test rows are identifiable by the `loadtest-` user prefix.
- CPU and RAM for the platform run are taken from the stress run at the same levels.

## 8. Conclusion — criterion #1: **PARTIAL**

- **Platform capacity: PASS.** 10 RPS was sustained for 60 s with 0% errors and p95 31 ms. The stress test reached 50 RPS with 0% errors. TPM and provider TPS are measured and reported.
- **Real-provider capacity: FAIL at the target.** The current GigaChat quota returns HTTP 429 at 0.2 RPS, and end-to-end p50 is about 2.3 s. Reaching 10 RPS end-to-end requires a higher provider quota or concurrency limit (and/or an app-side provider concurrency limiter with 429 backoff). It cannot be shown with the current account.
