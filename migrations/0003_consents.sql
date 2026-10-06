-- v0.7: PD-processing consent and pre-consent onboarding state.
-- Designed for PostgreSQL. Apply manually on existing installations.

CREATE TABLE IF NOT EXISTS telegram_onboarding (
    telegram_user_id BIGINT PRIMARY KEY,
    state VARCHAR(32) NOT NULL
        CHECK (state IN ('consent_pending', 'active', 'revoked')),
    pending_action VARCHAR(32)
        CHECK (pending_action IN ('delete_confirm', 'export_confirm')),
    pending_action_expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_consents (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    kind VARCHAR(32) NOT NULL CHECK (kind IN ('pd_processing')),
    version VARCHAR(32) NOT NULL,
    text_sha256 CHAR(64) NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_consents_active
    ON user_consents (telegram_user_id, kind)
    WHERE revoked_at IS NULL;
