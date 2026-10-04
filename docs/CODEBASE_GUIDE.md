# Codebase Guide — "where do I look?"

Baseline documented: v0.5 (Integration Kit, branch `v0.5-integration`). The code is the source of truth; see [Known technical debt and inconsistencies](#known-technical-debt-and-inconsistencies) where docs disagree.

Status tags used below:

- **[Implemented]** — working behavior covered by tests.
- **[Placeholder]** — partial or dev-only implementation that exists so the pipeline runs end to end. Do not rely on it as a finished feature.
- **[Tech debt]** — known inconsistency or shortcut.

---

## Read these 7 files first (15–20 minutes)

| # | File | What to take away |
|---|---|---|
| 1 | `app/main.py` | The FastAPI app: three routers, `/health`, and `mount_miniapp_static` serving the built frontend at `/miniapp`. |
| 2 | `app/container.py` | `build_container` picks the LLM provider (fake/openai/gigachat/deepseek via `build_llm_provider`), memory vs Postgres repositories, memory vs Redis checkpoints. |
| 3 | `app/api/miniapp.py` | The public API. `authenticate_miniapp_user` turns `X-Telegram-Init-Data` into an internal user. |
| 4 | `config/workflows/soften.yaml` | Stage order, inputs/outputs of each stage, and the validate → generate retry. |
| 5 | `app/workflows/engine.py` | `WorkflowEngine._run`: the deterministic loop that executes stages, checkpoints and retries. |
| 6 | `app/artifacts/models.py` | Every typed artifact passed between stages, and `DeliveryResponse` returned to clients. |
| 7 | `frontend/src/features/assist/MainScreen.tsx` | How the UI builds and sends an assist request and renders the result. |

After these, everything else is detail.

---

## Where to start

- **Backend entry:** `app/main.py` → `lifespan` → `build_container` (`app/container.py`) → `AppSettings` (`app/settings.py`, env/`.env`).
- **Workflows are YAML, not Python classes:** `config/workflows/{soften,decode,help-say}.yaml`, loaded by `load_workflow_manifest` (`app/config.py`).
- **Stages:** `app/stages/*.py`, resolved by name via `StageRegistry.resolve` (`app/stages/registry.py`).
- **Prompts:** Markdown in `skills/`, described by YAML manifests in `config/skills/`.
- **Frontend entry:** `frontend/src/main.tsx` → `App.tsx` → `src/app/AppProviders.tsx` + `src/app/AppRouter.tsx`.

---

## I want to understand one user request end-to-end

Example: user taps **Soften** in the Mini App.

| # | Layer | Where |
|---|---|---|
| 1 | Frontend action | `MainScreen.onSubmit` (`frontend/src/features/assist/MainScreen.tsx`) validates non-empty and `MAX_ASSIST_TEXT_LENGTH` (10 000), builds `MiniAppAssistBody { text, language }`, adds `relationship_id` **only** when the user explicitly picked a non-default relationship. |
| 2 | State hook | `useAssistMutation` (`frontend/src/state/queries.ts`). |
| 3 | API adapter | `miniAppApi.assist` (`frontend/src/api/miniapp.ts`) → `requestJson` (`frontend/src/api/client.ts`) adds `X-Telegram-Init-Data` from `getRawInitData()` (`frontend/src/telegram/`) and calls `POST /v1/miniapp/assist/soften` via `buildApiUrl`. Errors become `ApiError`. |
| 4 | FastAPI endpoint | `miniapp_assist` (`app/api/miniapp.py`), body `MiniAppAssistBody`. |
| 5 | Authentication | Dependency `authenticate_miniapp_user` → `TelegramMiniAppAuth.validate` (`app/integrations/telegram/miniapp_auth.py`): HMAC check, `auth_date` freshness. 401 on failure, 503 if no bot token. |
| 6 | User identity | `container.users.get_or_create_from_telegram` (`app/repositories/users.py` or `postgres_users.py`) → `UserRecord` with internal `user_id`. |
| 7 | Ownership check | If `relationship_id` was sent: `relationships.get(user_id, relationship_id)`; 404 if not owned. Language = body → `user.language_code` → `"ru"`. |
| 8 | Engine | `container.engine.execute(WorkflowName.SOFTEN, AssistRequest(...))` → `WorkflowEngine._run` (`app/workflows/engine.py`). |
| 9 | `receive` | `ReceiveStage` → `MessageRequest`. |
| 10 | `safety` | `SafetyStage` → `SafetyDecision`. **[Placeholder]** — see [Placeholder behavior](#placeholder-behavior). |
| 11 | `context` | `ContextStage` (`app/stages/context.py`): if no `relationship_id`, uses `user.default_relationship_id`; loads record + rules → `RelationshipContext`. No relationship → empty context, `ruleset_version=0`. |
| 12 | `plan` | `PlanningStage` (`app/stages/planning.py`): rules sorted by `priority` become `constraints`; `tone` from `communication_style`; skill name/version/output from YAML `config`. → `GenerationPlan`. |
| 13 | `generate` | `GenerationStage` (`app/stages/generation.py`) → `SkillLoader.load_bundle("soften")` (`app/skills/loader.py`) → tool `llm_generate` = `LLMGenerateTool` (`app/tools/llm/tool.py`) → `generate` on the provider selected by `LLM_PROVIDER`: `FakeStructuredLLMProvider`, `OpenAIStructuredLLMProvider`, `GigaChatStructuredLLMProvider` or `DeepSeekStructuredLLMProvider`. Output validated into `SoftenResult`. |
| 14 | `validate` | `ValidationStage` (`app/stages/validation.py`) → `ValidationResult`. **[Placeholder: partial checks]**. On `retry` the engine re-runs `generate` (max 2 attempts, from YAML). |
| 15 | `deliver` | `DeliveryStage` (`app/stages/delivery.py`) → `DeliveryResponse { status, text, structured_result }`. |
| 16 | Response | FastAPI serializes `DeliveryResponse` (`app/artifacts/models.py`). |
| 17 | Frontend render | `ResultView` (`frontend/src/features/assist/ResultView.tsx`) uses `parseDeliveryResult` (`results.ts`) to show soften/decode/help-say fields, blocked/error notices, and copy via `copyText`. |

Every stage output passes through `validate_artifact` in the engine, and a checkpoint is saved before and after each stage.

---

## I want to understand authentication

| Concern | Where | Status |
|---|---|---|
| initData validation | `TelegramMiniAppAuth.validate`, `TelegramInitData`, `TelegramWebAppUser`, `TelegramInitDataError` in `app/integrations/telegram/miniapp_auth.py` | [Implemented] HMAC-SHA-256 (`WebAppData` key), duplicate-field rejection, future/expired `auth_date` (`TELEGRAM_INIT_DATA_MAX_AGE_SECONDS`). |
| FastAPI dependency | `authenticate_miniapp_user` → `AuthenticatedMiniAppUser` in `app/api/miniapp.py` | [Implemented] Re-validates on every request; no session tokens. |
| User identity | `UserRepository.get_or_create_from_telegram` (`app/repositories/users.py`, `app/repositories/postgres_users.py`) | [Implemented] `telegram_user_id` → internal `user_id`; profile fields refreshed. |
| Tenant isolation | Every `RelationshipRepository` method takes `user_id` first; in-memory store is keyed by `(user_id, relationship_id)` | [Implemented] |
| Bot identity | `_resolve_internal_user` in `app/api/telegram.py` | [Implemented] Same user mapping for bot/inline. |
| Webhook auth | `telegram_webhook` checks `X-Telegram-Bot-Api-Secret-Token` against `TELEGRAM_WEBHOOK_SECRET` | [Implemented] |
| Frontend side | `getRawInitData` in `frontend/src/telegram/`; `VITE_DEV_INIT_DATA` honored only in DEV builds | [Implemented] |

**Public vs internal:**

- `/v1/miniapp/*` — public; identity only from validated initData.
- `/v1/assist/*` (`app/api/http.py`) — **trusted/internal**. It takes `AssistRequest` including a caller-supplied `user_id` and has **no authentication**. Never expose it to clients. [Tech debt: not enforced in code]
- `/v1/telegram/webhook` — Telegram only.

---

## I want to understand relationships and rules

- **Models:** `RelationshipRecord`, `RelationshipCreate`, `RelationshipUpdate` (`app/repositories/relationships.py`); `RelationshipRule`, `RelationshipContext` (`app/artifacts/models.py`); API views `RelationshipView`, `RelationshipCreateBody`, `RelationshipUpdateBody`, `RuleBody` (`app/api/miniapp.py`).
- **Repositories:** `RelationshipRepository`, `InMemoryRelationshipRepository` (`app/repositories/relationships.py`); `PostgresRelationshipRepository` (`app/repositories/postgres_relationships.py`). Users: `app/repositories/users.py`, `app/repositories/postgres_users.py`.
- **Default relationship:** stored as `UserRecord.default_relationship_id`.
  - Set by `PUT /v1/miniapp/me/default-relationship/{id}` (`set_default_relationship`).
  - Auto-set on create when the user has no default or `set_as_default=true` (`create_relationship`).
  - Cleared to `None` (not reassigned) when the default relationship is deleted (`delete_relationship`).
  - Resolved at request time in `ContextStage`.
- **`ruleset_version`:** starts at `1` on create; `+1` on relationship update, `add_rule`, `update_rule`, `delete_rule`. Passed into `RelationshipContext` and LLM metadata. `0` means "no relationship context".
- **PATCH semantics:** `relation_type: null` means "unchanged" (cannot clear).
- **CRUD endpoints:** all in `app/api/miniapp.py` (see `docs/SYSTEM_MAP.md` for the route list).
- **Frontend:** `RelationshipsScreen.tsx` (list, create, delete, set default, explicit no-default notice), `RelationshipDetailsScreen.tsx` (edit, rules), `RuleForm.tsx`, `format.ts` (`relationshipLabel`) under `frontend/src/features/relationships/`. Server state via hooks in `frontend/src/state/queries.ts`, which invalidate `bootstrapQueryKey` after every mutation. The frontend never computes defaults or versions itself.

---

## I want to understand the AI layer

- **Skills/prompts:**
  - Manifests: `config/skills/core/{safety,relationship-rules,output-contract}.yaml`, `config/skills/workflows/{soften,decode,help-say}.yaml`.
  - Bodies: `skills/core/*.md`, `skills/workflows/*.md`.
  - `SkillLoader.load_bundle` loads the workflow skill plus its `requires_core_skills`, sorted by `priority`, and joins them as system instructions.
- **OpenAI:** `OpenAIStructuredLLMProvider` (`app/tools/llm/openai_provider.py`) calls `client.responses.parse(..., text_format=<Pydantic model>)`; SDK `max_retries` from `OPENAI_MAX_RETRIES`.
- **GigaChat:** `GigaChatStructuredLLMProvider` (`app/tools/llm/gigachat_provider.py`) posts to `{GIGACHAT_BASE_URL}/chat/completions` with `response_format={type: json_schema, schema, strict: true}` and parses `choices[0].message.content`. The access token is obtained from `GIGACHAT_CREDENTIALS`, cached until 60 s before its 30-minute expiry, and refreshed once on HTTP 401.
- **DeepSeek:** `DeepSeekStructuredLLMProvider` (`app/tools/llm/deepseek_provider.py`) calls the Responses API (`client.responses.create`) at `DEEPSEEK_BASE_URL` with `text.format={type: json_schema, name, schema}`.
- Both use `app/tools/llm/schema.py` to send the Pydantic model's JSON Schema and to parse and validate the returned JSON. Errors never include credentials, tokens or prompt/response text. Provider selection is `build_llm_provider` in `app/container.py`.
- **Provider comparison:** `python scripts/compare_providers.py --providers fake,openai,gigachat,deepseek` runs the synthetic cases in `evals/cases/*.json` through the real engine and reports provider/model/workflow/success/latency/schema_valid/result. It does no scoring. Providers that are not configured are reported, not called.
- **Structured output parsing:** `LLMGenerateTool` (`app/tools/llm/tool.py`) maps the plan's `output_schema` to a model in `ARTIFACT_MODELS` and calls `model.model_validate(...)`. The engine then runs `validate_artifact` again.
- **Fake provider:** `FakeStructuredLLMProvider` (`app/tools/llm/fake.py`) — [Placeholder: dev/test only] deterministic output, no network.
- **Why the LLM cannot control workflow progression:**
  - Stage order, inputs and outputs come from YAML manifests, executed by `WorkflowEngine._run`.
  - The LLM is only reachable inside `GenerationStage` through the `llm_generate` tool.
  - Its output must validate against a fixed Pydantic artifact; it cannot name the next stage.
  - Retry decisions come from `ValidationResult.status` and the YAML `retry` block, not from LLM text.
- **Retries / checkpoints:**
  - Retry loop: `WorkflowEngine._run` (`validate` → `retry_stage`, bounded by `retry.max_attempts`).
  - Checkpoints: `WorkflowEngine._checkpoint` → `CheckpointStore` (`app/checkpoints/base.py`), `InMemoryCheckpointStore` (`memory.py`), `RedisCheckpointStore` (`redis_store.py`, key `request:{id}`, TTL `REDIS_CHECKPOINT_TTL_SECONDS`).
  - Resume: `WorkflowEngine.resume`.
  - There is no provider fallback (no secondary model).

---

## I want to change an AI prompt or skill

1. Find the manifest in `config/skills/**` (e.g. `config/skills/workflows/soften.yaml`) and its `source` Markdown (e.g. `skills/workflows/soften.md`).
2. Edit the Markdown body. Keep the output contract consistent with the artifact model (`SoftenResult`, etc. in `app/artifacts/models.py`) — fields are enforced by Pydantic.
3. If behavior changes meaningfully, bump `version` in the skill manifest and `skill_version` in the stage `config` of `config/workflows/<workflow>.yaml`.
4. Do not change stage order or artifact names from a prompt.
5. Run `pytest -q` (notably `tests/test_manifest_contracts.py`). With `LLM_PROVIDER=fake`, prompt text has no effect, so test real behavior with `LLM_PROVIDER=openai|gigachat|deepseek` (or compare them with `scripts/compare_providers.py`).

---

## I want to add a new workflow

Current extension points (no new architecture):

1. **Name:** add to `WorkflowName` (`app/artifacts/models.py`).
2. **Result artifact:** add a model and register it in `ARTIFACT_MODELS`; optionally document in `config/artifacts/`.
3. **Manifest:** `config/workflows/<name>.yaml` with the stage list (copy `soften.yaml`), `plan.config.skill/skill_version/output_artifact`, `generate.produces`, `validate.requires` and `retry`, `deliver.requires`.
4. **Skill:** `config/skills/workflows/<name>.yaml` + `skills/workflows/<name>.md`.
5. **Stages with hard-coded result names (must edit):**
   - `ValidationStage` — tuple `("soften_result", "decode_result", "help_say_result")` and per-type checks.
   - `DeliveryStage` — per-artifact branches that build `text`.
6. **Fake provider:** add a branch in `FakeStructuredLLMProvider.generate`, otherwise it raises.
7. **API:** `/v1/miniapp/assist/{workflow}` picks it up via `WorkflowName`. `/v1/assist/*` in `app/api/http.py` needs an explicit route. Telegram needs `parse_inline_query` / `parse_message_command` (`app/integrations/telegram/`) and `_workflow_title` (`app/api/telegram.py`).
8. **Frontend:** `WorkflowName` in `frontend/src/api/types.ts`, the `workflows` list in `MainScreen.tsx`, parsing in `results.ts`, rendering in `ResultView.tsx`, i18n keys in `frontend/src/i18n/`.
9. **Tests:** `tests/test_engine.py`, `tests/test_manifest_contracts.py`, `tests/test_miniapp.py`, plus frontend tests next to changed files.

---

## I want to change the frontend

| Area | Where |
|---|---|
| App shell / router | `src/App.tsx` (`initTelegram`), `src/app/AppRouter.tsx` (`BootstrapGate`, `NavigationBar`, `AppLayout`, `AppRoutes`, `AppRouter` using `HashRouter`), `src/app/AppProviders.tsx` (`AppProviders`, `useAppLanguage`, `useT`). |
| Features | `src/features/assist/`, `src/features/onboarding/`, `src/features/relationships/`, `src/features/settings/`. |
| API adapter | Thin adapter over the shared client: `src/api/client.ts` (`apiClient`, `requestJson`, `buildApiUrl`, `ApiError` = `MiniAppApiError`), `src/api/miniapp.ts` (`miniAppApi`), `src/api/types.ts` (re-exports generated DTOs), `src/api/errors.ts` (`toErrorStatus`). |
DTOs in `src/generated/openapi.ts` are generated from `docs/openapi.json` by openapi-typescript (`npm run generate`);
| State ownership | Server data: TanStack Query in `src/state/queries.ts`. UI-only state (workflow, draft text, one-off relationship, language): Zustand `src/state/uiStore.ts`, memory only. Language override persisted in `localStorage` by `src/i18n/`. |
| i18n | `src/i18n/ru.ts`, `en.ts`, `index.ts`. |
| Telegram adapter | `src/telegram/` — the only place allowed to import `@tma.js/*` (ESLint `no-restricted-imports` in `frontend/eslint.config.js`). |
| UI wrappers | `src/components/ui/` — the only place allowed to import `@telegram-apps/telegram-ui`; exports `Button`, `Checkbox`, `Input`, `Select`, `TextArea`, `Card`, `List`, `ListCell`, `ListSection`, `Modal`, `Notice`, `Row`, `Spinner`, `Stack`, `UiRoot`. Shared states in `src/components/common/`. |

Production build uses Vite base `/miniapp/`; API paths stay absolute `/v1/miniapp/...`.

---

## I want to debug a request

1. **Request enters:** browser devtools → `POST /v1/miniapp/assist/{workflow}`; backend `miniapp_assist` in `app/api/miniapp.py`.
2. **Auth checked:** `authenticate_miniapp_user` → `TelegramMiniAppAuth.validate`. 401 = bad/expired initData; 503 = `TELEGRAM_BOT_TOKEN` unset.
3. **Context loaded:** `ContextStage` — check `user.default_relationship_id` and `relationships.get(user_id, id)`. Empty context ⇒ `ruleset_version=0`.
4. **Workflow selected:** `WorkflowName` path param → `load_workflow_manifest` → `config/workflows/<name>.yaml` → `StageRegistry.resolve`.
5. **LLM called:** `GenerationStage` → `LLMGenerateTool.execute` → provider chosen in `build_container` (`LLM_PROVIDER`). Tool failures surface as `RuntimeError` from the stage.
6. **Response validated:** `validate_artifact` in `WorkflowEngine._run`; `ValidationStage` semantic checks; retry loop; `DeliveryStage` returns `status="error"` if validation still fails. Inspect `container.trace` (`RequestTraceSink`) and checkpoints (`container.checkpoints.load(request_id)`).
7. **Frontend displays:** `useAssistMutation` result → `ResultView` / `parseDeliveryResult`; transport errors → `ErrorState` via `toErrorStatus`.

---

## I want to run the project locally

Backend (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
pytest -q
uvicorn app.main:app --reload
```

Backend (bash):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
pytest -q
uvicorn app.main:app --reload
```

`.env.example` defaults: `LLM_PROVIDER=fake`, `RELATIONSHIP_BACKEND=memory`, `CHECKPOINT_BACKEND=memory`. Mini App endpoints additionally need `TELEGRAM_BOT_TOKEN`.

Frontend (`frontend/`, Node ≥ 20):

```powershell
cd frontend
npm install        # or npm ci when package-lock.json is present
npm run typecheck  # tsc -b --force
npm run lint
npm test           # vitest run
npm run build      # tsc -b --force && vite build -> frontend/dist
npm run dev        # Vite dev server, proxies /v1 to localhost:8000
```

Docker:

```powershell
docker compose up -d          # postgres, redis and app (see tech debt #3)
python scripts/init_db.py     # fresh DB schema
```

---

## Files that are infrastructure, not business logic

You usually don't need these first: `app/persistence/`, `migrations/`, `app/checkpoints/`, `app/observability/`, `app/tools/registry.py`, `app/tools/base.py`, `config/tools/`, `config/providers/`, `config/persistence/`, `config/runtime/`, `config/integrations/`, `config/artifacts/` (descriptive manifests), `scripts/`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `frontend/vite.config.ts`, `frontend/tsconfig*.json`, `frontend/eslint.config.js`, `frontend/src/test/`.

---

## Common mistakes

- Trusting a client-supplied `user_id`. Mini App identity comes only from validated initData.
- Bypassing `authenticate_miniapp_user` or reading `initDataUnsafe` for identity.
- Exposing `/v1/assist/*` publicly.
- Do not log raw initData or raw message text, and do not write either to long-term storage such as PostgreSQL, logs, or traces. The current implementation does temporarily store request text in workflow checkpoints: WorkflowEngine serializes api_request (including text) into checkpoint state. Checkpoints live either in memory or in Redis under request:{id} and expire according to REDIS_CHECKPOINT_TTL_SECONDS. This TTL-bound checkpoint storage is the only current exception; do not extend raw message text into durable storage.
- Letting LLM output decide stage order or retries.
- Duplicating repository/default/version logic in the frontend instead of re-fetching bootstrap.
- Silently changing relationship/default semantics (e.g., auto-picking a default after deletion).
- Importing `@tma.js/*` or `@telegram-apps/telegram-ui` outside their adapter folders.
- Treating `SafetyStage` or `ValidationStage` as complete (see below).

---

## Placeholder behavior

These are **partial implementations**, not finished features:

- **`SafetyStage` (`app/stages/safety.py`) — partial, deterministic placeholder.** It adds fixed constraints for `decode` and returns `allow` / `allow_with_constraints`. It **never returns `block`**, so the engine's blocked path is currently unreachable. It is **not** a safety or moderation system.
- **`ValidationStage` (`app/stages/validation.py`) — partial.** Checks that key fields are non-empty and, for `soften`, that the rewrite does not contain phrases quoted in `avoid` relationship rules (`avoided_phrases_absent`, deterministic, `app/stages/constraints.py`; a violation triggers the declared `generate` retry). `schema_valid` and `language_valid` are hard-coded `True`. It is **not** complete validation. Actual schema enforcement comes from `LLMGenerateTool` Pydantic validation and `validate_artifact`.
- **`FakeStructuredLLMProvider`** — dev/test only.
- **In-memory demo data** — `build_container` seeds user `u-1` with default relationship `partner-1` (`InMemoryRelationshipRepository.demo()`) in memory mode.

---

## Known technical debt and inconsistencies

1. Version metadata: resolved in v0.5 — `app/main.py` (FastAPI `version` and `/health`), `pyproject.toml`, `frontend/package.json` and `clients/typescript/package.json` are all `0.5.0`.
2. Resolved in v0.5 — `IMPLEMENTATION_STATUS.md` now records executed frontend and client checks.
3. `docker-compose.yml` `app` service has no profile, so `docker compose up -d` also builds and starts the app.
4. `Dockerfile` falls back to `npm install` when `package-lock.json` is missing or invalid (non-deterministic build).
5. `docs/ARCHITECTURE.md` §7 lists paths that don't exist (`app/api/miniapp/`, `app/api/telegram/`, `app/workflows/soften.py`, `app/artifacts/request.py`, `app/repositories/rules.py`).
6. `/v1/assist/*` accepts a caller-supplied `user_id` with no auth; `docs/MINIAPP_API.md` calls it internal in one line but does not explain the risk.
7. Webhook path is `/v1/telegram/webhook` (not `/webhook`).
8. `SafetyStage` and `ValidationStage` are partial (see above).
9. Checkpoints (`WorkflowEngine._serialize_state`) contain the raw request text until TTL expiry.
10. In-memory mode seeds demo data.
11. Resolved in v0.5 — `docs/MINIAPP_API.md` documents `POST /v1/miniapp/auth`, PATCH `null` = unchanged, and "deleting the default leaves no default".
