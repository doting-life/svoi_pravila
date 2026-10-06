# Telegram Mini App API — v0.6

The machine-readable contract is [`docs/openapi.json`](openapi.json) / [`docs/openapi.yaml`](openapi.yaml), with sample payloads in [`docs/fixtures/`](fixtures/). For client integration (TypeScript client, examples, error handling) see [INTEGRATION.md](INTEGRATION.md); for a local run see [QUICKSTART.md](QUICKSTART.md).

## Authentication contract

The Mini App sends the **raw** value of `window.Telegram.WebApp.initData` in every API request:

```http
X-Telegram-Init-Data: <raw Telegram.WebApp.initData query string>
```

The backend does not trust `initDataUnsafe` and does not accept a client-supplied `user_id` for Mini App operations. It verifies Telegram's HMAC-SHA-256 signature and checks `auth_date` against `TELEGRAM_INIT_DATA_MAX_AGE_SECONDS`.

After validation, `telegram_user_id` is mapped to an internal `user_id`. The first valid request creates the user; later requests update non-sensitive Telegram profile fields such as display name, username, and language code.

The HTTP auth layer is intentionally stateless (since v0.3): it validates `initData` on each Mini App request instead of minting a second application session token. That keeps identity derivation simple and avoids another credential/session store during MVP.

If `TELEGRAM_BOT_TOKEN` is not configured, every Mini App endpoint returns `503`. Missing, tampered or expired `initData` returns `401`.

### Consent required (`403`)

Every Mini App endpoint requires a current personal-data consent given in the Telegram bot via `/start`. Consent is checked after `initData` validation and before any user record is created or read. Without consent the response is `403` with this exact flat body (OpenAPI schema `ConsentRequiredError`, no FastAPI `detail` wrapper):

```json
{
  "error": "consent_required",
  "message": "Personal data processing consent is required. Open the Telegram bot and send /start.",
  "bot_command": "/start"
}
```

The TypeScript client exposes `MiniAppApiError.isConsentRequired` / `isConsentRequiredError(err)`, which match only this exact shape. The frontend renders a consent screen with a link to `https://t.me/DotingLifeBot?start=consent` (override with `VITE_BOT_USERNAME`) and a re-check button.

## Auth

`POST /v1/miniapp/auth`

Validates `initData`, creates or updates the user and returns `{ user, auth_date }`. It is optional (every endpoint authenticates the same way), but useful as an explicit sign-in check.

## Bootstrap

`GET /v1/miniapp/bootstrap`

Returns the authenticated user plus all owned relationships and rules. It is intended as the initial Mini App load endpoint.

## Relationships

- `GET /v1/miniapp/relationships`
- `POST /v1/miniapp/relationships`
- `PATCH /v1/miniapp/relationships/{relationship_id}`
- `DELETE /v1/miniapp/relationships/{relationship_id}`
- `PUT /v1/miniapp/me/default-relationship/{relationship_id}`

The first created relationship automatically becomes the default unless another default already exists. A caller can also set `set_as_default: true` when creating a relationship.

`PATCH` only changes fields that are present and non-`null`; sending `null` (or omitting a field) leaves it unchanged. Deleting the current default relationship leaves the user with no default (`default_relationship_id: null`); another relationship is not promoted automatically.

A relationship can only be read, changed, selected, or used by its owner. Relationships and rules that don't exist or belong to another user both return `404`.

## Rules

- `POST /v1/miniapp/relationships/{relationship_id}/rules`
- `PUT /v1/miniapp/relationships/{relationship_id}/rules/{rule_id}`
- `DELETE /v1/miniapp/relationships/{relationship_id}/rules/{rule_id}`

Every rule mutation increments `ruleset_version`. The workflow receives the current rules through `RelationshipContext`.

## AI requests

`POST /v1/miniapp/assist/{workflow}` where workflow is one of:

- `soften`
- `decode`
- `help-say`

Request body:

```json
{
  "text": "Ты опять всё отложил",
  "relationship_id": null,
  "language": "ru"
}
```

`relationship_id` is optional. When omitted, `ContextStage` resolves the authenticated user's `default_relationship_id`. The client cannot choose another user's relationship: ownership is checked before workflow execution.

## Database migration

Fresh development databases can still use:

```bash
python scripts/init_db.py
```

Existing v0.2 PostgreSQL installations should additionally apply:

```bash
psql "${DATABASE_URL/postgresql+asyncpg/postgresql}" -f migrations/0002_users_and_miniapp.sql
```

The migration adds the `users` table and a `NOT VALID` relationship foreign key so legacy rows are not destroyed. Legacy application users should be mapped explicitly before validating the constraint.

## Frontend hosting (v0.4)

The built Mini App is served by FastAPI at `/miniapp/` when `MINIAPP_STATIC_DIR` (default `frontend/dist`) exists; otherwise `/miniapp` returns 404. The frontend calls only `/v1/miniapp/*` and sends raw `Telegram.WebApp.initData` in `X-Telegram-Init-Data` on every request. `/v1/assist/*` remains internal and is not used by the frontend.

