# System Map

Baseline documented: branch `v0.4`, commit `625e479`. For navigation and placeholders/tech debt, see `docs/CODEBASE_GUIDE.md`.

---

## Component diagram

```mermaid
flowchart LR
	subgraph Client["Telegram client"]
		TG["Telegram app"]
		MA["Mini App (React, frontend/src)"]
	end

	subgraph Backend["FastAPI (app/main.py)"]
		STATIC["/miniapp static mount<br/>mount_miniapp_static"]
		MINI["/v1/miniapp/*<br/>app/api/miniapp.py"]
		ASSIST["/v1/assist/* (internal)<br/>app/api/http.py"]
		HOOK["/v1/telegram/webhook<br/>app/api/telegram.py"]
		AUTH["TelegramMiniAppAuth<br/>miniapp_auth.py"]
		ENGINE["WorkflowEngine<br/>app/workflows/engine.py"]
		STAGES["Stages app/stages/*<br/>receive, context, plan, generate, deliver: implemented<br/>⚠ SafetyStage: PARTIAL placeholder, never blocks<br/>⚠ ValidationStage: PARTIAL, non-empty checks only"]
		SKILLS["SkillLoader<br/>config/skills + skills/"]
		LLMTOOL["LLMGenerateTool<br/>app/tools/llm/tool.py"]
		REPOS["User / Relationship repositories<br/>app/repositories/*"]
		CKPT["CheckpointStore<br/>app/checkpoints/*"]
	end

	PG[("PostgreSQL")]
	RD[("Redis")]
	OAI["OpenAI Responses API"]
	TGAPI["Telegram Bot API"]

	TG --> MA
	MA -->|"GET /miniapp/"| STATIC
	MA -->|"X-Telegram-Init-Data"| MINI
	TG -->|"updates"| HOOK
	MINI --> AUTH
	MINI --> REPOS
	MINI --> ENGINE
	ASSIST --> ENGINE
	HOOK --> REPOS
	HOOK --> ENGINE
	HOOK --> TGAPI
	ENGINE --> STAGES
	ENGINE --> CKPT
	STAGES --> REPOS
	STAGES --> SKILLS
	STAGES --> LLMTOOL
	LLMTOOL --> OAI
	REPOS --> PG
	CKPT --> RD
```

Backends are chosen in `build_container` (`app/container.py`): fake vs OpenAI LLM, memory vs Postgres repositories, memory vs Redis checkpoints.

## Request sequence (Mini App assist)

```mermaid
sequenceDiagram
	participant UI as MainScreen
	participant API as miniapp_assist
	participant Auth as TelegramMiniAppAuth
	participant Users as UserRepository
	participant Eng as WorkflowEngine
	participant LLM as LLMGenerateTool

	UI->>API: POST /v1/miniapp/assist/{workflow}
	API->>Auth: validate(initData)
	Auth-->>API: TelegramInitData or 401
	API->>Users: get_or_create_from_telegram
	API->>Eng: execute(workflow, AssistRequest)
	Eng->>Eng: receive, safety [PARTIAL placeholder: never blocks], context, plan
	Eng->>LLM: generate (structured output)
	LLM-->>Eng: result artifact
	Eng->>Eng: validate [PARTIAL: non-empty checks only] (retry generate if needed), deliver
	Eng-->>API: DeliveryResponse
	API-->>UI: JSON
```

---

## Components

| Component | Location | Responsibility | Status |
|---|---|---|---|
| App entrypoint | `app/main.py` | Routers, `/health`, `/miniapp` static | Implemented |
| Container | `app/container.py` | Dependency wiring | Implemented |
| Settings | `app/settings.py` | Env configuration | Implemented |
| Mini App API | `app/api/miniapp.py` | Auth, bootstrap, relationships, rules, assist | Implemented |
| Internal assist API | `app/api/http.py` | `/v1/assist/*`, no auth | Implemented; tech debt (trust boundary not enforced) |
| Telegram webhook | `app/api/telegram.py` | Bot and inline mode | Implemented |
| initData auth | `app/integrations/telegram/miniapp_auth.py` | HMAC validation | Implemented |
| Workflow engine | `app/workflows/engine.py` | Stage loop, checkpoints, retry/resume | Implemented |
| Manifests | `config/workflows/*.yaml` | Stage order, retry | Implemented |
| `ReceiveStage`, `ContextStage`, `PlanningStage`, `GenerationStage`, `DeliveryStage` | `app/stages/` | Pipeline steps | Implemented |
| `SafetyStage` | `app/stages/safety.py` | Fixed constraints, never blocks | **Placeholder (partial)** |
| `ValidationStage` | `app/stages/validation.py` | Non-empty checks + soften avoided-phrase check | **Placeholder (partial)** |
| Skills | `app/skills/loader.py`, `config/skills/`, `skills/` | Prompt bundles | Implemented |
| LLM providers | `app/tools/llm/` | OpenAI / fake | Implemented / fake is dev-only |
| Repositories | `app/repositories/` | Users, relationships, rules | Implemented (memory + Postgres) |
| Checkpoints | `app/checkpoints/` | Transient workflow state | Implemented (memory + Redis) |
| Frontend | `frontend/src/` | Mini App UI | Implemented |
| Deployment | `Dockerfile`, `docker-compose.yml` | Image + infra | Implemented; tech debt (see guide) |

---

## Data ownership

| Data | Owner | Storage |
|---|---|---|
| User identity, `default_relationship_id` | `UserRepository` | Memory / PostgreSQL |
| Relationships, rules, `ruleset_version` | `RelationshipRepository` | Memory / PostgreSQL |
| Workflow state (incl. raw request text) | `CheckpointStore` | Memory / Redis with TTL |
| Server data on the client | TanStack Query (`frontend/src/state/queries.ts`) | Browser memory |
| UI-only state | Zustand (`frontend/src/state/uiStore.ts`) | Browser memory |
| Language override | `frontend/src/i18n/` | `localStorage` |

The frontend never computes defaults or `ruleset_version`; it re-fetches bootstrap after mutations.

---

## Interface boundaries

| Interface | Audience | Auth |
|---|---|---|
| `/miniapp/*` | Browser (static) | None |
| `/v1/miniapp/*` | Mini App (public) | `X-Telegram-Init-Data` |
| `/v1/assist/*` | Trusted internal callers only | None — caller-supplied `user_id` |
| `/v1/telegram/webhook` | Telegram only | `X-Telegram-Bot-Api-Secret-Token` |
| `/health` | Ops | None |

Mini App routes: `POST /auth`, `GET /bootstrap`, `GET|POST /relationships`, `PATCH|DELETE /relationships/{id}`, `PUT /me/default-relationship/{id}`, `POST /relationships/{id}/rules`, `PUT|DELETE /relationships/{id}/rules/{rule_id}`, `POST /assist/{workflow}` (all under `/v1/miniapp`).

---

## Change-impact map

| If you change… | Also check |
|---|---|
| An artifact in `app/artifacts/models.py` | `ARTIFACT_MODELS`, stages, skills output contract, `frontend/src/api/types.ts`, `results.ts`, tests |
| A workflow YAML | `tests/test_manifest_contracts.py`, `StageRegistry`, retry behavior |
| A skill prompt | Matching artifact model; `skill_version` in workflow YAML |
| Mini App API shape | `frontend/src/api/*`, `frontend/src/state/queries.ts`, `docs/MINIAPP_API.md`, `tests/test_miniapp.py` |
| Auth | `authenticate_miniapp_user`, `tests/test_miniapp.py`, frontend `getRawInitData` |
| Repository semantics | Both memory and Postgres implementations, `ContextStage`, frontend screens |
| Static hosting / Vite base | `app/main.py`, `app/settings.py`, `frontend/vite.config.ts`, `Dockerfile`, `tests/test_miniapp_static.py` |
| A new workflow | See "I want to add a new workflow" in `docs/CODEBASE_GUIDE.md` |

---

## Source of truth

Code and tests on `v0.4` win. Where the existing docs (`docs/ARCHITECTURE.md`, `docs/MINIAPP_API.md`, `IMPLEMENTATION_STATUS.md`) and version metadata disagree with the code, see "Known technical debt and inconsistencies" in `docs/CODEBASE_GUIDE.md`.
