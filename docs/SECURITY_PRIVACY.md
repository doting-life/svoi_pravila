# Security and Privacy

## Data minimization

По умолчанию продукт не хранит полный текст пользовательских сообщений после завершения запроса.

## Persistent data

Разрешено хранить:
- relationship configuration;
- user-authored rules;
- preferences;
- ruleset history;
- technical telemetry;
- explicit user feedback.

## Ephemeral data

Raw message content и intermediate artifacts могут храниться только на время выполнения запроса и должны иметь TTL.

## Secrets

- API keys только через secret manager/environment;
- никогда не передавать keys в skill context;
- redact secrets from logs;
- provider errors sanitise before persistent logging.

## Consent to personal data processing

- Consent is given only in the Telegram bot: `/start` shows the consent text with «Согласен» / «Не согласен» buttons. `/start` is idempotent and keeps a current valid consent.
- Without a current consent (missing, declined, revoked or for an outdated consent version) the bot does not create a user and does not run workflows; every `/v1/miniapp/*` endpoint returns `403` with the flat `ConsentRequiredError` body before any user record is created or read.
- Consent records are stored in the `consents` table (`migrations/0003_consents.sql`); no raw conversation text is stored.
- Users can revoke consent, export their data (generated in memory and sent as a Telegram document) and delete their account (two-phase confirmation; Redis checkpoints are cleaned after the DB commit; telemetry is anonymised).
- The Mini App frontend detects the exact `403 consent_required` contract and shows a dedicated screen with a link to @DotingLifeBot and the `/start` instruction instead of a generic error.

## Access boundaries

Mini App / Telegram clients не должны получать внутренние prompts, hidden rules, provider credentials или internal validation traces.

## Provider boundary

Manifest каждого tool должен явно указывать, отправляет ли он raw user text третьему provider.
