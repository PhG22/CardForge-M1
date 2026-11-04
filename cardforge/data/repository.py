from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional, Protocol

from .records import CardRecord


class DatabaseUnavailable(RuntimeError):
    """cards.db ausente, vazio ou ilegível — bloqueia o início de uma run (CV-SD-04, pre-run block)."""


class CardRepository(Protocol):
    def find_print(self, set_code: str, number: str) -> Optional[CardRecord]: ...
    def find_latest_print_by_name(self, name_key: str, prefer_set: str = "") -> Optional[CardRecord]: ...
    def all_name_keys(self) -> list[str]: ...
    def all_set_codes(self) -> list[str]: ...
    def close(self) -> None: ...


def normalize_number(number: str) -> str:
    """
    Normaliza o número de colecionador para busca: minúsculas e sem zeros à esquerda
    ("0092" -> "92", "007a" -> "7a", "0" -> "0").
    """
    return str(number).strip().lower().lstrip("0") or "0"


_PRINT_SELECT = """
    SELECT p.print_id, o.name, o.mana_cost, o.type_line, p.rarity, p.set_code,
           p.collector_number_raw, p.layout,
           (SELECT GROUP_CONCAT(color_code, '') FROM oracle_color oc WHERE oc.oracle_id = o.oracle_id)
    FROM card_print p
    JOIN oracle_card o ON o.oracle_id = p.oracle_id
"""


_PRIMARY_SET_TYPES = ", ".join(f"'{t}'" for t in (
    "core", "expansion", "masters", "commander", "draft_innovation", "starter", "duel_deck", "box", "token"))


class SqliteCardRepository:
    """
    Acesso somente leitura ao cards.db. Os dados das cartas ficam no SQLite;
    só a lista de nomes é carregada em memória pelo NameIndex (Pi 3B tem 1 GB de RAM — CV-DP).
    """

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.conn: Optional[sqlite3.Connection] = None
        self.open()

    def open(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            raise DatabaseUnavailable(f"card database missing: {self.path}")
        try:
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            count = conn.execute("SELECT COUNT(*) FROM card_print").fetchone()[0]
        except sqlite3.Error as e:
            raise DatabaseUnavailable(f"card database unreadable: {self.path} ({e})") from e
        if count == 0:
            conn.close()
            raise DatabaseUnavailable(f"card database empty: {self.path}")
        self.conn = conn

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def reopen(self) -> None:
        self.close()
        self.open()

    @staticmethod
    def _to_record(row) -> CardRecord:
        return CardRecord(
            print_id=row[0],
            name=row[1],
            mana_cost=row[2] or "",
            type_line=row[3] or "",
            rarity=row[4] or "",
            set_code=(row[5] or "").upper(),
            collector_number=row[6] or "",
            layout=row[7] or "",
            color_identity=frozenset(row[8] or ""),
        )

    def find_print(self, set_code: str, number: str) -> Optional[CardRecord]:
        row = self.conn.execute(
            _PRINT_SELECT + " WHERE p.set_code = ? AND p.collector_number_norm = ?",
            (set_code.lower(), normalize_number(number)),
        ).fetchone()
        return self._to_record(row) if row else None

    def find_latest_print_by_name(self, name_key: str, prefer_set: str = "") -> Optional[CardRecord]:
        """
        Impressão mais recente da carta. Prefere a edição lida pelo OCR (quando o número falhou)
        e coleções "normais" a reimpressões especiais (The List, Secret Lair...).
        """
        row = self.conn.execute(
            _PRINT_SELECT
            + " JOIN card_name n ON n.oracle_id = p.oracle_id"
            + " LEFT JOIN mtg_set s ON s.set_code = p.set_code"
            + " WHERE n.name_key = ?"
            + " ORDER BY (p.set_code = ?) DESC, (p.set_code NOT IN ('plst', 'sld')) DESC,"
            + f" (s.set_type IN ({_PRIMARY_SET_TYPES})) DESC, p.released_at DESC LIMIT 1",
            (name_key.lower(), prefer_set.lower()),
        ).fetchone()
        return self._to_record(row) if row else None

    def all_name_keys(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT name_key FROM card_name")]

    def all_set_codes(self) -> list[str]:
        return [r[0].upper() for r in self.conn.execute("SELECT set_code FROM mtg_set")]

    def metadata(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT key, value FROM db_metadata"))
