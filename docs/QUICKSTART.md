# Quickstart — local Mini App API in 5 minutes

Runs the backend with the **fake LLM, in-memory repositories and in-memory
checkpoints**. You don't need PostgreSQL, Redis, OpenAI or a real Telegram bot.
Data is lost on restart.

Requirements: Python 3.11+, Node 20+ (only for the frontend/TypeScript parts).

## 1. Install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## 2. Run the backend (fake/memory)

The Mini App API needs `TELEGRAM_BOT_TOKEN` to validate initData. Without it,
`/v1/miniapp/*` returns 503. Locally, any token-shaped value works because you
sign initData with the same value.

```bash
export TELEGRAM_BOT_TOKEN=123456:TEST_TOKEN
export LLM_PROVIDER=fake RELATIONSHIP_BACKEND=memory CHECKPOINT_BACKEND=memory TELEGRAM_ENABLED=false
uvicorn app.main:app --port 8000
```

PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN = "123456:TEST_TOKEN"
$env:LLM_PROVIDER = "fake"; $env:RELATIONSHIP_BACKEND = "memory"; $env:CHECKPOINT_BACKEND = "memory"; $env:TELEGRAM_ENABLED = "false"
uvicorn app.main:app --port 8000
```

These are already the defaults. Setting them explicitly overrides a local
`.env` that points at real services. Check: `curl http://localhost:8000/health`
returns `{"status":"ok","version":"0.6.0"}`.

## 3. Call the API

In a second terminal from the repo root (same `TELEGRAM_BOT_TOKEN`):

**Python** — auth → create relationship → add rule → bootstrap → soften:

```bash
python examples/python/miniapp_client.py --base-url http://localhost:8000
```

**JavaScript** (plain fetch, Node 20+):

```bash
export TELEGRAM_INIT_DATA=$(python -c "from examples.python.miniapp_client import sign_init_data; print(sign_init_data('123456:TEST_TOKEN'))")
node examples/javascript/fetch-example.mjs http://localhost:8000
```

**curl**: see [examples/curl/README.md](../examples/curl/README.md).

A request without the header returns `401 {"detail":"Missing Telegram initData"}`.

The fake LLM returns deterministic placeholder output (for example, `soften`
echoes a lightly adjusted version of the input). Use it to check integration
and response shape, not answer quality.

## 4. Mini App frontend (optional)

```bash
cd frontend
npm install            # also links ../clients/typescript
npm run dev            # http://localhost:5173, proxies /v1 -> http://localhost:8000
```

Outside Telegram there is no `initData`, so API calls fail with 401 and the UI
shows its "reopen the Mini App from Telegram" error. To serve the built app from FastAPI at `/miniapp/`, run
`npm run build` and restart the backend (`MINIAPP_STATIC_DIR` defaults to
`frontend/dist`).

## 5. Run the tests

```bash
pytest                                              # backend, contract, examples
cd clients/typescript && npm install && npm test    # TS client + TS/JS examples
cd frontend && npm test                             # Mini App
```

## Next

- [INTEGRATION.md](INTEGRATION.md): contract, auth, errors, TypeScript client.
- [MINIAPP_API.md](MINIAPP_API.md): endpoint behavior.
- [PRODUCTION_RUNTIME.md](PRODUCTION_RUNTIME.md): OpenAI, PostgreSQL, Redis, Telegram.
