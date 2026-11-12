-- 001_init.sql
-- Schema v1: users, runs, cards
-- Every card belongs to exactly one run and is owned by exactly one user.

BEGIN;

-- ---------------------------------------------------------------------------
-- Enum types
-- ---------------------------------------------------------------------------
CREATE TYPE run_status  AS ENUM ('running', 'completed', 'failed', 'cancelled');
CREATE TYPE scan_status AS ENUM ('pending', 'success', 'failed');

-- ---------------------------------------------------------------------------
-- Keeps updated_at current on every UPDATE
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          text        NOT NULL,
    username       text        NOT NULL,
    password_hash  text        NOT NULL,          -- full Argon2id/bcrypt encoded string
    is_active      boolean     NOT NULL DEFAULT true,
    last_login_at  timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT users_email_not_blank    CHECK (btrim(email) <> ''),
    CONSTRAINT users_username_not_blank CHECK (btrim(username) <> ''),
    CONSTRAINT users_hash_not_blank     CHECK (password_hash <> '')
);

CREATE UNIQUE INDEX users_email_lower_uq    ON users (lower(email));
CREATE UNIQUE INDEX users_username_lower_uq ON users (lower(username));

CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- runs
-- ---------------------------------------------------------------------------
CREATE TABLE runs (
    id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_by        bigint      REFERENCES users (id) ON DELETE SET NULL,
    status            run_status  NOT NULL DEFAULT 'running',
    started_at        timestamptz NOT NULL DEFAULT now(),
    stopped_at        timestamptz,
    cards_processed   integer     NOT NULL DEFAULT 0,
    successful_scans  integer     NOT NULL DEFAULT 0,
    failed_scans      integer     NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT runs_counts_non_negative
        CHECK (cards_processed >= 0 AND successful_scans >= 0 AND failed_scans >= 0),
    CONSTRAINT runs_counts_consistent
        CHECK (successful_scans + failed_scans <= cards_processed),
    CONSTRAINT runs_stop_after_start
        CHECK (stopped_at IS NULL OR stopped_at >= started_at),
    CONSTRAINT runs_stopped_iff_finished
        CHECK ((status = 'running') = (stopped_at IS NULL))
);

CREATE INDEX runs_started_at_idx ON runs (started_at DESC);
CREATE INDEX runs_status_idx     ON runs (status);
CREATE INDEX runs_started_by_idx ON runs (started_by);

CREATE TRIGGER runs_set_updated_at
    BEFORE UPDATE ON runs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- cards
-- ---------------------------------------------------------------------------
CREATE TABLE cards (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           bigint      NOT NULL REFERENCES runs  (id) ON DELETE CASCADE,
    owner_id         bigint      NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    card_identifier  text        NOT NULL,
    scan_status      scan_status NOT NULL DEFAULT 'pending',
    scanned_at       timestamptz,
    error_message    text,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT cards_identifier_not_blank
        CHECK (btrim(card_identifier) <> ''),
    CONSTRAINT cards_scanned_iff_done
        CHECK ((scan_status = 'pending') = (scanned_at IS NULL)),
    CONSTRAINT cards_error_only_on_failure
        CHECK (error_message IS NULL OR scan_status = 'failed')
);

CREATE INDEX cards_run_id_idx   ON cards (run_id, scan_status);
CREATE INDEX cards_owner_id_idx ON cards (owner_id);

COMMIT;
