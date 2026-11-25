-- 002_sync_keys.sql
-- Lets the sorter machine upload a finished run from its local SQLite buffer
-- safely: each run and card carries the UUID it was given locally, so a retried
-- upload (e.g. after a network drop) never creates duplicates.

BEGIN;

ALTER TABLE runs  ADD COLUMN local_uuid uuid;   -- NULL for runs not created by a sorter machine
ALTER TABLE cards ADD COLUMN local_uuid uuid;

CREATE UNIQUE INDEX runs_local_uuid_uq  ON runs  (local_uuid);
CREATE UNIQUE INDEX cards_local_uuid_uq ON cards (local_uuid);

COMMIT;
