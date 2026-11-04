from __future__ import annotations

import gzip
import io
import json
import logging
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .records import COLOR_NAMES
from .repository import normalize_number
from .schema import SCHEMA_SQL, SCHEMA_VERSION

log = logging.getLogger(__name__)

SCRYFALL_API = "https://api.scryfall.com"
# A Scryfall exige User-Agent e Accept explícitos em todas as requisições.
HEADERS = {"User-Agent": "CardForge-M1/1.0", "Accept": "application/json"}

# Layouts que não representam cartas "jogáveis"; o nome delas só é indexado se não houver outro dono.
_TOKEN_LAYOUTS = {"token", "double_faced_token", "emblem"}
_LOW_PRIORITY_LAYOUTS = {"art_series"}
GZIP_MAGIC = bytes([0x1F, 0x8B])


@dataclass
class BulkEntry:
    updated_at: str
    download_uri: str
    size: int = 0


@dataclass
class SyncReport:
    updated: bool
    prints: int = 0
    names: int = 0
    sets: int = 0
    bulk_updated_at: str = ""
    seconds: float = 0.0

    def __str__(self) -> str:
        if not self.updated:
            return f"already up to date (bulk {self.bulk_updated_at})"
        return (f"updated ({self.prints} prints, {self.names} names, {self.sets} sets, "
                f"bulk {self.bulk_updated_at}, {self.seconds:.0f}s)")


def _name_priority(card: dict, is_face: bool) -> int:
    """Prioridade de quem fica com um name_key quando vários oracles têm o mesmo nome (ex.: tokens "Spirit")."""
    layout = card.get("layout", "")
    if layout in _LOW_PRIORITY_LAYOUTS:
        return 0
    base = 1 if layout in _TOKEN_LAYOUTS else 3
    return base - (1 if is_face else 0) + 1


def _merge_faces(card: dict, key: str) -> str:
    """Campos de cartas de várias faces ficam em card_faces; junta-os como "A // B"."""
    if card.get(key):
        return card[key]
    faces = card.get("card_faces") or []
    parts = [f.get(key, "") for f in faces if f.get(key)]
    return " // ".join(parts)


def iter_jsonl(stream: io.BufferedIOBase) -> Iterator[dict]:
    """Lê JSON Lines, compactado em gzip ou não (o servidor pode já ter descompactado o transporte)."""
    is_gzip = stream.read(2) == GZIP_MAGIC
    stream.seek(0)
    raw = gzip.open(stream, "rb") if is_gzip else stream
    with io.TextIOWrapper(raw, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def build_database(cards: Iterable[dict], sets: list[dict], dest: Path,
                   bulk_updated_at: str = "", sets_updated_at: str = "") -> tuple[int, int]:
    """
    Constrói um cards.db novo em `dest` a partir das cartas do bulk "default_cards" e da lista de sets.
    Devolve (prints, names).
    """
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(dest)
    try:
        conn.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;")
        conn.executescript(SCHEMA_SQL)
        conn.executemany("INSERT INTO color VALUES (?, ?)", COLOR_NAMES.items())

        conn.executemany(
            "INSERT OR IGNORE INTO mtg_set VALUES (?, ?, ?, ?, ?)",
            [(s["code"].lower(), s.get("name", ""), s.get("set_type"),
              (s.get("parent_set_code") or None), s.get("released_at")) for s in sets],
        )

        # name_key -> (prioridade, oracle_id, is_face). Resolvido em memória e gravado no fim.
        names: dict[str, tuple[int, str, bool]] = {}
        prints = 0
        for card in cards:
            if card.get("digital"):  # MTGO / Arena não existem fisicamente
                continue
            oracle_id = card.get("oracle_id") or next(
                (f.get("oracle_id") for f in card.get("card_faces") or [] if f.get("oracle_id")), None)
            if not oracle_id:
                continue
            name = card.get("name", "")
            conn.execute(
                "INSERT OR IGNORE INTO oracle_card VALUES (?, ?, ?, ?)",
                (oracle_id, name, _merge_faces(card, "mana_cost"), _merge_faces(card, "type_line")),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO oracle_color VALUES (?, ?)",
                [(oracle_id, c) for c in card.get("color_identity", []) if c in COLOR_NAMES],
            )

            keys = [(name.lower(), False)]
            if " // " in name:
                keys += [(face.strip().lower(), True) for face in name.split(" // ")]
            # Nome impresso alternativo (Universes Beyond / Godzilla, ex.: "The Mechanist, Tactical Tinkerer").
            flavor_names = [card.get("flavor_name")] + [f.get("flavor_name") for f in card.get("card_faces") or []]
            keys += [(fn.lower(), True) for fn in flavor_names if fn]
            for key, is_face in keys:
                prio = _name_priority(card, is_face)
                if key and (key not in names or prio > names[key][0]):
                    names[key] = (prio, oracle_id, is_face)

            set_code = card.get("set", "").lower()
            number = str(card.get("collector_number", ""))
            if set_code and number:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO card_print VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (card["id"], set_code, normalize_number(number), number, oracle_id,
                     card.get("rarity"), card.get("released_at"), card.get("layout")),
                )
                prints += cur.rowcount

        conn.executemany(
            "INSERT INTO card_name VALUES (?, ?, ?)",
            [(k, v[1], v[2]) for k, v in names.items()],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO db_metadata VALUES (?, ?)",
            [("bulk_updated_at", bulk_updated_at), ("sets_updated_at", sets_updated_at),
             ("schema_version", SCHEMA_VERSION)],
        )
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return prints, len(names)


class ScryfallSyncService:
    """
    Atualiza o espelho local do Scryfall (CV-SD-04). Só deve ser chamado quando nenhuma run está ativa.
    O banco é construído num arquivo temporário e substitui o cards.db de forma atômica.
    """

    def __init__(self, db_path: str | os.PathLike, timeout: float = 60.0):
        self.db_path = Path(db_path)
        self.timeout = timeout

    def _get(self, url: str, **kw):
        import requests  # importado aqui: a identificação nunca precisa de rede (RNF15)
        r = requests.get(url, headers=HEADERS, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def fetch_bulk_index(self) -> BulkEntry:
        for entry in self._get(f"{SCRYFALL_API}/bulk-data").json().get("data", []):
            if entry.get("type") == "default_cards":
                uri = entry.get("jsonl_download_uri")
                if not uri:
                    raise RuntimeError("Scryfall bulk entry 'default_cards' has no jsonl_download_uri")
                return BulkEntry(entry["updated_at"], uri, entry.get("size", 0))
        raise RuntimeError("Scryfall bulk entry 'default_cards' not found")

    def fetch_sets(self) -> list[dict]:
        sets, url = [], f"{SCRYFALL_API}/sets"
        while url:
            page = self._get(url).json()
            sets.extend(page.get("data", []))
            url = page.get("next_page") if page.get("has_more") else None
        return sets

    def local_bulk_updated_at(self) -> Optional[str]:
        if not self.db_path.exists():
            return None
        try:
            conn = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT value FROM db_metadata WHERE key = 'bulk_updated_at'").fetchone()
            finally:
                conn.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def sync(self, force: bool = False,
             before_replace: Optional[Callable[[], None]] = None) -> SyncReport:
        """
        `before_replace` é chamado logo antes da troca atômica do arquivo
        (o VisionModule usa-o para fechar a conexão aberta — necessário no Windows).
        """
        t0 = time.time()
        bulk = self.fetch_bulk_index()
        if not force and self.local_bulk_updated_at() == bulk.updated_at:
            return SyncReport(updated=False, bulk_updated_at=bulk.updated_at)

        sets = self.fetch_sets()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_db = self.db_path.with_name(self.db_path.name + ".tmp")

        log.info("downloading Scryfall default_cards (%s bytes)...", bulk.size or "?")
        with tempfile.TemporaryFile() as gz:
            with self._get(bulk.download_uri, stream=True) as r:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    gz.write(chunk)
            gz.seek(0)
            log.info("building %s...", tmp_db)
            prints, names = build_database(iter_jsonl(gz), sets, tmp_db,
                                           bulk_updated_at=bulk.updated_at,
                                           sets_updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

        if before_replace:
            before_replace()
        os.replace(tmp_db, self.db_path)
        return SyncReport(True, prints, names, len(sets), bulk.updated_at, time.time() - t0)
