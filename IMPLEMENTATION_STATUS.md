v0.6

## Core already implemented

- Deterministic YAML-driven workflow engine.
- Strict stage dependency checks.
- Typed Pydantic artifacts.
- Markdown/YAML skill loading.
- Tool registry.
- Resume after failed stage and manifest-controlled retry.
- Technical tracing without raw prompt/message logging.
- FastAPI assist endpoints for `soften`, `decode`, and `help-say`.

## Production adapters from v0.2

- OpenAI Structured Outputs provider through the Responses API SDK parser.
- PostgreSQL async persistence.
- Redis checkpoint store with TTL.
- Telegram Bot webhook, webhook-secret verification, Inline Mode, and direct commands.
- Environment-driven dependency wiring and lifecycle cleanup.
- Docker Compose, database bootstrap, and Telegram webhook setup scripts.

## Implemented in v0.3

- **Telegram Mini App authentication** based on server-side validation of raw `Telegram.WebApp.initData`.
- HMAC-SHA-256 signature validation and `auth_date` freshness enforcement.
- Internal `User` identity separate from Telegram's user id.
- `telegram_user_id -> user_id` binding with create/update on a valid Telegram request.
- `default_relationship_id` on the user profile.
- User repository with in-memory and PostgreSQL implementations.
- Full relationship CRUD.
- Full relationship-rule CRUD.
- Ruleset version increment on mutable relationship/rule changes.
- Mini App bootstrap endpoint.
- Mini App authenticated AI endpoint that does not accept a trusted `user_id` from the client.
- Default relationship resolution inside the existing `ContextStage`.
- Telegram bot/inline requests now resolve the same internal user identity as the Mini App.
- Ownership enforcement preventing one user from selecting or using another user's relationship.
- PostgreSQL migration `migrations/0002_users_and_miniapp.sql` for existing v0.2 installations.
- Mini App YAML manifest and API documentation.

## Implemented in v0.4

- Telegram Mini App frontend (`frontend/`, React + TypeScript + Vite, `@telegram-apps/telegram-ui`).
- Bootstrap gate: users without relationships are routed to onboarding.
- Screens: Onboarding, Main (soften/decode/help-say with per-request relationship override), Relationships (create/delete/set default), Relationship details (edit, rule CRUD), Settings (ru/en override).
- Frontend consumes only `/v1/miniapp/*`; identity comes from `X-Telegram-Init-Data`.
- FastAPI serves the built frontend at `/miniapp` when `MINIAPP_STATIC_DIR` (default `frontend/dist`) exists; API routes are unaffected.
- Multi-stage `Dockerfile` (frontend build + Python runtime) and `app` service in `docker-compose.yml`.

## Implemented in v0.5 — Integration Kit

- Unified version metadata `0.5.0` (FastAPI app, `/health`, `pyproject.toml`, `frontend/package.json`, `clients/typescript/package.json`).
- Deterministic OpenAPI export `scripts/export_openapi.py` → `docs/openapi.json` / `docs/openapi.yaml` with `--check`; contract tests in `tests/test_openapi_contract.py`.
- Validated sample payloads in `docs/fixtures/` (`tests/test_contract_samples.py`).
- TypeScript client `clients/typescript` (`@svoi-pravila/miniapp-client`): DTOs are generated from `docs/openapi.json` by `openapi-typescript` with `--default-non-nullable false` into the generated `src/generated/openapi.ts` (`npm run generate` / `npm run check:generated`); the old custom Node type generator was removed. On top of the generated types sit the handwritten runtime-independent `createMiniAppClient({ baseUrl, getInitData, fetch })` (fetch is injected and required), `MiniAppApiError` (incl. 422 validation lists) and runtime result guards (`getStructuredResult`, `isSoftenResult`, `isDecodeResult`, `isHelpSayResult`, `isDeliveryResponse`); Vitest suite.
- Frontend `src/api/` is a thin adapter over the client package; rule ids are handled as `number | null` and optional response fields with backend defaults via `aliases ?? []`, `rules ?? []`, `priority ?? 0`, per the generated contract types.
- Dockerfile frontend stage builds with the local client package (`/src/frontend` + `/src/clients/typescript`).
- Examples: Python (`examples/python`, smoke-tested in-process), TypeScript and plain JavaScript (tested in the client suite), curl (`tests/test_examples_curl.py` checks coverage of every Mini App OpenAPI operation).
- Docs: `docs/INTEGRATION.md`, `docs/QUICKSTART.md` (fake/memory runtime verified end-to-end against a live uvicorn process), `docs/MINIAPP_API.md` updated.
- No backend behavior changes.

## Multi-provider LLM support

- `LLM_PROVIDER=fake|openai|gigachat|deepseek`. The provider is selected in `build_llm_provider` (`app/container.py`). Credentials and model are validated in `AppSettings`.
- `GigaChatStructuredLLMProvider`: REST v1 chat completions, strict `json_schema` response_format, cached access token (refreshed 60 s before its 30-minute expiry, one refresh on 401).
- `DeepSeekStructuredLLMProvider`: Responses API with `text.format` JSON schema.
- Every provider payload is parsed and validated with the requested Pydantic model, then validated again by `LLMGenerateTool`. WorkflowEngine, GenerationStage, LLMGenerateTool, artifacts, API, OpenAPI and frontend are unchanged.
- Comparison harness: `scripts/compare_providers.py` with synthetic cases in `evals/cases/`. It does no scoring.
- Tests use mocked transports/clients only (`tests/test_llm_providers.py`). No real provider calls have been made, so real-provider quality is unverified.

## Current validation

- Backend: 103 pytest tests pass (`pytest -q`), including 31 mocked GigaChat/DeepSeek/provider-selection tests, OpenAPI contract, fixtures, Python example smoke and curl coverage tests; `python scripts/export_openapi.py --check` passes.
- TypeScript client: `check:generated` (openapi-typescript `--check`), `tsc --noEmit`, `npm run build` and 33 Vitest tests pass (2 files), including runtime-independence tests (required injected `fetch`/`getInitData`, no global `fetch` use, no runtime globals in `src/`).
- Frontend: `tsc -b --force`, ESLint (0 errors, 8 pre-existing `react-refresh/only-export-components` warnings), 54 Vitest tests and `vite build` pass. The Docker frontend stage was reproduced outside Docker (clean `npm ci` + build with the local client package); `docker build` itself was not run in this environment.
- The test suite covers Telegram initData integrity/expiry, user creation, relationship CRUD, rule creation, default relationship resolution, tenant isolation, workflow retry/resume, OpenAI adapter contracts, Redis checkpoints, and Telegram parsing.

## Deliberately deferred

- Alembic migration framework; v0.3 ships an explicit SQL migration.
- Dedicated production moderation/safety provider.
- Persistent telemetry backend / dashboards.
- Queue/worker layer for long-running workloads.
- Billing/subscription enforcement.
- Optional first-party session tokens; v0.3 re-validates Telegram `initData` per Mini App request.

The Workflow/Stage/Skill/Tool/Artifact core remains unchanged by these additions.
