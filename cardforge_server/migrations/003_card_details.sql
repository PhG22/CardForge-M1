-- 003_card_details.sql
-- What the app needs to show a run's results (RF22) and the user's collection
-- (RF24) without a second lookup in the Scryfall mirror, which lives only on the
-- Pi (cards.db). The sorter machine sends these fields with every card.
--
-- * runs:  error_code / message say why a run ended; synced_at = when the machine
--          uploaded it.
-- * cards: the tray the card fell into (RF19 "category"), its number in the run,
--          the identity read by the vision (name, set, collector number, ...).
--          An unidentified or face-down card (RF25 / RF39) has scan_status
--          'failed', a flag saying which, and no card_identifier.

BEGIN;

CREATE TYPE scan_flag AS ENUM ('unidentified', 'face_down');

ALTER TABLE runs
    ADD COLUMN error_code text,
    ADD COLUMN message    text,
    ADD COLUMN synced_at  timestamptz;

-- An unidentified card has no Scryfall id. Identified cards still must have one.
ALTER TABLE cards ALTER COLUMN card_identifier DROP NOT NULL;
ALTER TABLE cards DROP CONSTRAINT cards_identifier_not_blank;
ALTER TABLE cards
    ADD CONSTRAINT cards_identifier_not_blank
        CHECK (card_identifier IS NULL OR btrim(card_identifier) <> ''),
    ADD CONSTRAINT cards_success_has_identifier
        CHECK (scan_status <> 'success' OR card_identifier IS NOT NULL);

ALTER TABLE cards
    ADD COLUMN card_number      integer,          -- 1, 2, ... inside the run
    ADD COLUMN bin              smallint,         -- tray 1-6; NULL on the top-only bench
    ADD COLUMN flag             scan_flag,        -- why a failed scan failed
    ADD COLUMN card_name        text,
    ADD COLUMN set_code         text,
    ADD COLUMN collector_number text,
    ADD COLUMN color_identity   text,             -- e.g. "WU", "" = colorless
    ADD COLUMN type_line        text,
    ADD COLUMN mana_cost        text,
    ADD COLUMN rarity           text,
    ADD COLUMN match_method     text,
    ADD COLUMN confidence       integer,
    ADD COLUMN attempts         integer,          -- captures used (RF29)

    ADD CONSTRAINT cards_number_positive  CHECK (card_number IS NULL OR card_number >= 1),
    ADD CONSTRAINT cards_bin_range        CHECK (bin IS NULL OR bin BETWEEN 1 AND 6),
    ADD CONSTRAINT cards_flag_iff_failed  CHECK ((flag IS NOT NULL) = (scan_status = 'failed'));

CREATE UNIQUE INDEX cards_run_number_uq ON cards (run_id, card_number);

-- The collection: a user's identified cards, searched by name.
CREATE INDEX cards_collection_idx ON cards (owner_id, lower(card_name)) WHERE scan_status = 'success';

COMMIT;
