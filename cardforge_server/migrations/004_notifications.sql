-- 004_notifications.sql
-- RF38: the alerts of a run (RF27: a fault that paused it; the end of the run) are kept
-- here so the app can deliver them as notifications when it is in the background or the
-- phone is locked. The sorter machine sends each alert once it happens
-- (POST /machine/events); the app's background service waits for new rows
-- (GET /notifications?after=<id>&wait=25).
--
-- * user_id:  the user who started the run (the only one who receives the alert).
-- * event_id: chosen by the machine, so a resent alert is stored once.
-- * kind:     'fault'     = the run paused (code = FEED_TIMEOUT, DROP_FAILED, ...)
--             'run_ended' = the run finished (status completed / cancelled / failed).
-- Alerts older than 30 days are deleted when a new one arrives for the same user.

BEGIN;

CREATE TABLE notifications (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    event_id        uuid        NOT NULL,
    run_local_uuid  uuid,
    kind            text        NOT NULL,
    code            text,
    status          text,
    message         text,
    cards_processed integer,
    event_time      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT notifications_event_id_unique UNIQUE (event_id),
    CONSTRAINT notifications_kind_check CHECK (kind IN ('fault', 'run_ended')),
    CONSTRAINT notifications_status_check
        CHECK (status IS NULL OR status IN ('completed', 'cancelled', 'failed')),
    CONSTRAINT notifications_cards_non_negative CHECK (cards_processed IS NULL OR cards_processed >= 0)
);

CREATE INDEX notifications_user_id_id ON notifications (user_id, id);

COMMIT;
