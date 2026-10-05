# Running Svoi Pravila with Docker Compose

This guide is for someone who does not remember the architecture. All commands are for **Windows PowerShell**, run from the repository root (`D:\Projects\SvoiPravila`).

## What runs

| Service | What it is | Host port | Volume |
|---|---|---|---|
| `app` | FastAPI backend + built Telegram Mini App (served at `/miniapp/`) | `8000` | none |
| `postgres` | PostgreSQL 17 (users, relationships, rules) | `127.0.0.1:5432` | `postgres_data` |
| `redis` | Redis 7 (workflow checkpoints, AOF persistence) | `127.0.0.1:6379` | `redis_data` |
| `db-init` | One-shot job: `python scripts/init_db.py`, then exits | none | none |

How it fits together:

- The `Dockerfile` has two stages. Stage 1 (Node 20) copies `clients/typescript` and `frontend`, installs dependencies inside the image and runs `npm run build`. Stage 2 (Python 3.11) installs the backend and copies only `frontend/dist` from stage 1. Node.js is **not** in the final image, and host `node_modules` are never copied (see `.dockerignore`).
- `db-init` waits until PostgreSQL is healthy, then creates **missing** tables only (SQLAlchemy `create_all`). It never drops or recreates data, so it is safe on every start.
- `app` starts only after PostgreSQL and Redis are healthy and `db-init` has exited successfully. Compose forces `RELATIONSHIP_BACKEND=postgres`, `CHECKPOINT_BACKEND=redis` and the in-network `DATABASE_URL` / `REDIS_URL`; all other settings come from your `.env`.
- At startup the app preloads the workflow skills (`soften`, `decode`, `help-say`). If a skill is broken the app refuses to start.
- Secrets live only in `.env`, which is excluded from git and from the image.

## FIRST RUN

1. Install Docker Desktop (https://www.docker.com/products/docker-desktop/), start it and wait for "Engine running". Then check:

   ```powershell
   docker version
   docker compose version
   ```

2. Copy the example environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Configure local fake-provider settings. The defaults already work; make sure `.env` contains:

   ```dotenv
   LLM_PROVIDER=fake
   TELEGRAM_ENABLED=false
   ```

   No paid credentials are needed in this mode.

4. Build the images:

   ```powershell
   docker compose build
   ```

5. Start everything in the background:

   ```powershell
   docker compose up -d
   ```

6. Initialize the database. This happens **automatically** via the `db-init` service. To run it again manually (safe, non-destructive):

   ```powershell
   docker compose run --rm db-init
   ```

7. Check status. `app`, `postgres`, `redis` should be `healthy`; `db-init` should be `Exited (0)`:

   ```powershell
   docker compose ps -a
   ```

8. Open the health URL http://localhost:8000/health (expected `{"status":"ok"}`):

   ```powershell
   Invoke-RestMethod http://localhost:8000/health
   ```

9. Open the Mini App: http://localhost:8000/miniapp/

   In a normal browser the page loads, but API calls fail authentication: the Mini App API requires validated Telegram `initData`, which only exists inside Telegram. The page loading proves the frontend build is served.

Optional API smoke test (fake provider, no Telegram needed):

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/v1/assist/soften -ContentType 'application/json' -Body '{"user_id":"docker-smoke","text":"You never answer my messages."}'
```

Interactive API docs: http://localhost:8000/docs

## NORMAL START

```powershell
docker compose up -d
```

## STOP

```powershell
docker compose down
```

Stops and removes containers, **keeps** the PostgreSQL and Redis volumes.

## RESTART

```powershell
docker compose restart app
```

After editing `.env`, `restart` is not enough. Recreate the container:

```powershell
docker compose up -d --force-recreate app
```

## REBUILD AFTER CODE CHANGES

Backend, frontend, client, skills or config changes all require a rebuild:

```powershell
docker compose build
docker compose up -d
```

## VIEW LOGS

```powershell
docker compose logs -f app
docker compose logs -f postgres
docker compose logs -f redis
docker compose logs db-init
docker compose logs --tail 200 app
```

## CHECK STATUS

```powershell
docker compose ps -a
```

## RESET DEVELOPMENT DATABASE

> **WARNING: DESTRUCTIVE.** The command below **permanently deletes** all local Docker data for this project: every user, relationship, rule and Redis checkpoint. There is no undo.

```powershell
# DESTROYS the postgres_data and redis_data volumes
docker compose down -v
docker compose up -d
```

`db-init` creates an empty schema on the next start.

## RUN WITH FAKE PROVIDER

```dotenv
LLM_PROVIDER=fake
```

Deterministic canned output, no network calls, no credentials.

```powershell
docker compose up -d --force-recreate app
```

## SWITCH TO GIGACHAT

Edit `.env`:

```dotenv
LLM_PROVIDER=gigachat
GIGACHAT_CREDENTIALS=<authorization key from the Sber developer portal>
GIGACHAT_MODEL=<GigaChat model name>
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_BASE_URL=https://api.giga.chat/v1
GIGACHAT_AUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_TIMEOUT_SECONDS=30
GIGACHAT_CA_BUNDLE=/app/certs/russian_trusted_root_ca_pem.crt
```

Required: `GIGACHAT_CREDENTIALS` (the authorization key, not an access token; the app obtains and caches access tokens itself) and `GIGACHAT_MODEL`. Use `GIGACHAT_API_B2B` or `GIGACHAT_API_CORP` as scope if your account requires it. `GIGACHAT_CA_BUNDLE` is **required in Docker**: both GigaChat hosts (`ngw.devices.sberbank.ru`, `api.giga.chat`) use certificates issued by the Russian Trusted Root CA, which is not in the default Python/Debian trust store. Without it the app fails with `GigaChat auth request failed: ConnectError` (`CERTIFICATE_VERIFY_FAILED`). The public CA certificate is committed at `certs/russian_trusted_root_ca_pem.crt` (official source: https://www.gosuslugi.ru/crt, SHA-256 fingerprint `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`) and is copied into the image at `/app/certs/`. TLS verification stays enabled.

```powershell
docker compose up -d --force-recreate app
```

## SWITCH TO DEEPSEEK

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=<key from platform.deepseek.com>
DEEPSEEK_MODEL=<DeepSeek model name>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT_SECONDS=60
DEEPSEEK_MAX_RETRIES=2
```

Required: `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`.

```powershell
docker compose up -d --force-recreate app
```

## SWITCH TO SBER500 GATEWAY

OpenAI-compatible Sber500 / Disrupt gateway (`POST /chat/completions`, strict `json_schema` structured output, Bearer key). Set in `.env`:

```
LLM_PROVIDER=sber500
SBER500_API_KEY=<gateway API key>
SBER500_MODEL=gigachat-3-pro
SBER500_BASE_URL=https://shared1.multitool.works:4000/v1
SBER500_TIMEOUT_SECONDS=30
```

Required: `SBER500_API_KEY`.
`SBER500_MODEL` defaults to `gigachat-3-pro`; when explicitly set, it must not be empty.
No automatic retries (HTTP 429 is returned to the client as 429).
```powershell
docker compose up -d --force-recreate app
```

## SWITCH TO OPENAI

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=<key from platform.openai.com>
OPENAI_MODEL=<Structured-Outputs-capable model>
OPENAI_TIMEOUT_SECONDS=30
OPENAI_MAX_RETRIES=2
```

Required: `OPENAI_API_KEY`, `OPENAI_MODEL`.

```powershell
docker compose up -d --force-recreate app
```

Real providers make **paid** API calls on every assist request. Missing credentials make the app fail at startup with a settings error such as `DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek`. Never paste key values into issues, logs or docs.

## TELEGRAM REAL DEPLOYMENT

### A. Local Docker (this guide)

Everything runs on `localhost`. Telegram servers cannot reach `localhost`, so the webhook cannot work and the Mini App cannot authenticate outside Telegram. Keep `TELEGRAM_ENABLED=false`.

### B. Real Telegram Bot / Mini App

Telegram requires a **publicly reachable HTTPS URL** with a valid certificate (a server with a domain and TLS, or a tunnel such as Cloudflare Tunnel / ngrok forwarding to port 8000). `localhost` can never be a webhook URL.

With a public base URL `https://your-domain.example`, put in `.env`:

```dotenv
APP_ENV=production
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<token from @BotFather>
TELEGRAM_WEBHOOK_SECRET=<long random string: letters, digits, _ and - only>
TELEGRAM_WEBHOOK_URL=https://your-domain.example/v1/telegram/webhook
TELEGRAM_DEFAULT_WORKFLOW=soften
TELEGRAM_INIT_DATA_MAX_AGE_SECONDS=3600
```

- `TELEGRAM_BOT_TOKEN` calls the Bot API and validates Mini App `initData`.
- `TELEGRAM_WEBHOOK_SECRET` is registered with Telegram; Telegram sends it back in the `X-Telegram-Bot-Api-Secret-Token` header and the app rejects webhook calls without it. Required when `APP_ENV=production`.
- `TELEGRAM_WEBHOOK_URL` = public base URL + `/v1/telegram/webhook`.

Recreate the app, then register the webhook **once** (never automatic):

```powershell
docker compose up -d --force-recreate app
docker compose exec app python scripts/set_telegram_webhook.py
```

The script calls Telegram `setWebhook` with the URL, the secret and `allowed_updates = inline_query, message`.

Mini App URL to configure in @BotFather (`/newapp` or the bot Menu Button):

```
https://your-domain.example/miniapp/
```

## TROUBLESHOOTING

**Port 8000 already in use** (`Bind for 0.0.0.0:8000 failed`). Find the owner with `Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess`, stop it, or change the left side of `"8000:8000"` in `docker-compose.yml` (e.g. `"8080:8000"`). Same for 5432 / 6379 if PostgreSQL or Redis is installed locally.

**Docker daemon not running** (`error during connect`, `dockerDesktopLinuxEngine`). Start Docker Desktop and wait for "Engine running". If `docker` is not recognized, install Docker Desktop and open a new PowerShell window.

**postgres unhealthy**: `docker compose logs postgres`. Usual causes: port conflict, low disk space, or a volume created with different credentials (only fix: the destructive reset).

**redis unhealthy**: `docker compose logs redis`. Usually a port conflict or a corrupt AOF file (the destructive reset fixes it).

**app exits on startup**: `docker compose logs app`. Look for a settings `ValidationError` (bad `.env` value) or `Failed to preload workflow skill`. Also check `docker compose logs db-init`; if it failed, `app` will not start.

**GigaChat `auth failed with HTTP 400`** (`Can't decode 'Authorization' header`): `GIGACHAT_CREDENTIALS` must be the **Authorization key** shown in the Sber developer portal (a long Base64 string, about 100 characters, encoding `client_id:client_secret`), not the Client ID or Client Secret alone. Paste it without quotes and without the `Basic ` prefix.

**Missing LLM credentials**: the log names the missing variable. Fill it in `.env` or switch to `LLM_PROVIDER=fake`, then `docker compose up -d --force-recreate app`.

**Missing Telegram token**: `TELEGRAM_BOT_TOKEN is required when TELEGRAM_ENABLED=true`. Set the token or set `TELEGRAM_ENABLED=false`.

**Broken workflow skill detected during preload**: `RuntimeError: Failed to preload workflow skill 'soften': ...`. A manifest under `config/` or a Markdown source under `skills/` is missing or invalid. Fix it, then `docker compose build; docker compose up -d`.

**Frontend `/miniapp/` not found (404)**: the image has no `frontend/dist`. Check the build output for `npm run build` errors and run `docker compose build --no-cache`. Use the trailing slash: `/miniapp/`.

**Clean rebuild** (keeps data):

```powershell
docker compose down
docker compose build --no-cache
docker compose up -d
```

Reclaim disk space (keeps volumes): `docker builder prune -f; docker image prune -f`.
