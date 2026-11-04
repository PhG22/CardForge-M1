"""Schema do cards.db — espelho local do Scryfall (diagrama CV-ER / CV16)."""

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
CREATE TABLE mtg_set (
    set_code        VARCHAR(8) PRIMARY KEY,          -- minúsculo, como no Scryfall
    name            TEXT NOT NULL,
    set_type        TEXT,
    parent_set_code VARCHAR(8) REFERENCES mtg_set(set_code),
    released_at     DATE
);

CREATE TABLE oracle_card (
    oracle_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    mana_cost TEXT,
    type_line TEXT
);

CREATE TABLE card_name (
    name_key     TEXT PRIMARY KEY,                   -- nome em minúsculas
    oracle_id    TEXT NOT NULL REFERENCES oracle_card(oracle_id),
    is_face_name BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE card_print (
    print_id              TEXT PRIMARY KEY,          -- id do Scryfall
    set_code              VARCHAR(8) NOT NULL REFERENCES mtg_set(set_code),
    collector_number_norm TEXT NOT NULL,             -- "0092" -> "92"
    collector_number_raw  TEXT NOT NULL,
    oracle_id             TEXT NOT NULL REFERENCES oracle_card(oracle_id),
    rarity                TEXT,
    released_at           DATE,
    layout                TEXT,
    UNIQUE (set_code, collector_number_norm)
);

CREATE TABLE color (
    color_code CHAR(1) PRIMARY KEY,
    name       TEXT NOT NULL
);

-- Sem linhas em oracle_color = carta incolor (usado no mapeamento padrão RF20).
CREATE TABLE oracle_color (
    oracle_id  TEXT NOT NULL REFERENCES oracle_card(oracle_id),
    color_code CHAR(1) NOT NULL REFERENCES color(color_code),
    PRIMARY KEY (oracle_id, color_code)
);

CREATE TABLE db_metadata (
    key   TEXT PRIMARY KEY,                          -- bulk_updated_at, sets_updated_at, schema_version
    value TEXT
);

CREATE INDEX idx_card_name_oracle ON card_name(oracle_id);
CREATE INDEX idx_card_print_oracle ON card_print(oracle_id, released_at);
"""
