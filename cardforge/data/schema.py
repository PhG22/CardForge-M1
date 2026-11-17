"""Schema do cards.db — espelho local do Scryfall (diagrama CV-ER / CV16)."""

SCHEMA_VERSION = "2"   # 2: nomes impressos noutros idiomas (card_name.lang, card_print_name)

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
    is_face_name BOOLEAN NOT NULL DEFAULT 0,
    lang         TEXT NOT NULL DEFAULT 'en'          -- 'en' = nome oficial/flavor; 'pt' = nome impresso traduzido
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

-- Nome impresso de cada impressão noutro idioma (Scryfall all_cards: lang + printed_name).
-- A carta em português tem a mesma edição e número da inglesa ("U 0283 LCI · PT"), então o
-- título lido em português confirma a impressão achada por set + número.
CREATE TABLE card_print_name (
    set_code              VARCHAR(8) NOT NULL,
    collector_number_norm TEXT NOT NULL,
    lang                  TEXT NOT NULL,             -- 'pt'
    printed_name          TEXT NOT NULL,             -- "Chicote do Espadachim" (várias faces: "A // B")
    print_id              TEXT NOT NULL,             -- id do Scryfall da impressão nesse idioma
    PRIMARY KEY (set_code, collector_number_norm, lang)
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
    key   TEXT PRIMARY KEY,                          -- bulk_updated_at, sets_updated_at, schema_version,
                                                     -- printed_langs, localized_prints, localized_names
    value TEXT
);

CREATE INDEX idx_card_name_oracle ON card_name(oracle_id);
CREATE INDEX idx_card_print_oracle ON card_print(oracle_id, released_at);
"""
