"""Local run storage on the Pi: run.db (SQLite), tables local_run and local_cards (DB-ER).

* RF19 / RNF20: one record per card that physically left the platform, written in
  one transaction together with the run counters, right after the drop.
* RNF19: WAL journal + synchronous=FULL, so a committed card survives a power cut.
* Runs that were still running or paused when the Pi lost power are marked
  failed (INTERRUPTED) on the next start.
* sync_state / sync_attempts are kept for the server SyncWorker (DB-SD-01), which
  does not exist yet: every run stays "pending".

The results (RF22) and the inventory (RF24) are read from here.
"""

from __future__ import annotations

import csv
import io
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_run (
    local_uuid       TEXT PRIMARY KEY,
    status           TEXT NOT NULL,             -- running | paused | completed | cancelled | failed
    started_at       TEXT NOT NULL,             -- ISO 8601, UTC
    stopped_at       TEXT,
    cards_processed  INTEGER NOT NULL DEFAULT 0,
    successful_scans INTEGER NOT NULL DEFAULT 0,
    failed_scans     INTEGER NOT NULL DEFAULT 0,
    error_code       TEXT,
    message          TEXT,
    sync_state       TEXT NOT NULL DEFAULT 'pending',   -- pending | synced | failed
    sync_attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS local_cards (
    local_uuid       TEXT PRIMARY KEY,
    run_uuid         TEXT NOT NULL REFERENCES local_run(local_uuid),
    card_number      INTEGER NOT NULL,          -- 1, 2, ... inside the run
    card_identifier  TEXT,                      -- Scryfall print id; NULL when unidentified
    scan_status      TEXT NOT NULL,             -- identified | unidentified | face_down
    bin              INTEGER,                   -- tray 1-6 it fell into; NULL in top-only mode
    match_method     TEXT,
    confidence       INTEGER,
    attempts         INTEGER,
    card_name        TEXT,
    set_code         TEXT,
    collector_number TEXT,
    color_identity   TEXT,
    type_line        TEXT,
    mana_cost        TEXT,
    rarity           TEXT,
    scanned_at       TEXT NOT NULL,
    error_message    TEXT,
    UNIQUE (run_uuid, card_number)
);
CREATE INDEX IF NOT EXISTS idx_local_cards_name ON local_cards(card_name);

CREATE TABLE IF NOT EXISTS app_token (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

ACTIVE_STATUSES = ("running", "paused")
CARD_COLUMNS = ("card_identifier", "scan_status", "bin", "match_method", "confidence", "attempts",
                "card_name", "set_code", "collector_number", "color_identity", "type_line", "mana_cost",
                "rarity", "error_message")


@dataclass
class ScanRecord:
    """One card that was physically sorted."""
    scan_status: str                       # identified | unidentified | face_down
    bin: int | None
    card_identifier: str | None = None
    match_method: str | None = None
    confidence: int | None = None
    attempts: int | None = None
    card_name: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    color_identity: str | None = None
    type_line: str | None = None
    mana_cost: str | None = None
    rarity: str | None = None
    error_message: str | None = None


class RunStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def compact(self) -> None:
        """Fold the write-ahead log back into run.db and truncate it (frees space after a run)."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ------------------------------------------------------------ runs

    def recover_interrupted(self, now: str) -> int:
        """Runs left running/paused by a power cut or crash are closed as failed."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE local_run SET status = 'failed', stopped_at = ?, error_code = 'INTERRUPTED', "
                "message = 'The run was interrupted (power loss or software restart).' "
                "WHERE status IN ('running', 'paused')", (now,))
            return cur.rowcount

    def create_run(self, run_id: str, started_at: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO local_run (local_uuid, status, started_at) VALUES (?, 'running', ?)",
                               (run_id, started_at))

    def update_run(self, run_id: str, *, status: str, stopped_at: str | None = None,
                   error_code: str | None = None, message: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE local_run SET status = ?, stopped_at = ?, error_code = ?, message = ? WHERE local_uuid = ?",
                (status, stopped_at, error_code, message, run_id))

    def save_scan(self, run_id: str, card_number: int, scanned_at: str, record: ScanRecord) -> str:
        """Insert the card and update the run counters in one transaction (RNF20)."""
        card_uuid = str(uuid.uuid4())
        ok = record.scan_status == "identified"
        values = [getattr(record, c) for c in CARD_COLUMNS]
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    f"INSERT INTO local_cards (local_uuid, run_uuid, card_number, scanned_at, {', '.join(CARD_COLUMNS)}) "
                    f"VALUES (?, ?, ?, ?, {', '.join('?' * len(CARD_COLUMNS))})",
                    (card_uuid, run_id, card_number, scanned_at, *values))
                self._conn.execute(
                    "UPDATE local_run SET cards_processed = cards_processed + 1, "
                    "successful_scans = successful_scans + ?, failed_scans = failed_scans + ? WHERE local_uuid = ?",
                    (int(ok), int(not ok), run_id))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return card_uuid

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM local_run WHERE local_uuid = ?", (run_id,)).fetchone()
        return _run_dict(row) if row else None

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM local_run ORDER BY started_at DESC LIMIT ? OFFSET ?",
                                      (limit, offset)).fetchall()
        return [_run_dict(r) for r in rows]

    def run_results(self, run_id: str) -> dict | None:
        """Per-bin counts and the full card list of a run (RF22)."""
        run = self.get_run(run_id)
        if run is None:
            return None
        with self._lock:
            cards = [dict(r) for r in self._conn.execute(
                "SELECT * FROM local_cards WHERE run_uuid = ? ORDER BY card_number", (run_id,))]
        counts: dict[str, int] = {}
        for c in cards:
            key = str(c["bin"]) if c["bin"] is not None else "none"
            counts[key] = counts.get(key, 0) + 1
        statuses = {s: sum(c["scan_status"] == s for c in cards) for s in ("identified", "unidentified", "face_down")}
        return {"run": run, "bin_counts": counts, "scan_counts": statuses,
                "cards": [_card_dict(c) for c in cards]}

    # ------------------------------------------------------- inventory

    def inventory(self, query: str = "", limit: int = 30, offset: int = 0) -> tuple[list[dict], int]:
        """Identified cards of finished runs, grouped by name, with the quantity per bin.
        Cards of a run in progress are left out (RF23)."""
        where = ("c.scan_status = 'identified' AND r.status NOT IN ('running', 'paused')"
                 + (" AND lower(c.card_name) LIKE ?" if query else ""))
        params: list = [f"%{query.lower()}%"] if query else []
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(DISTINCT c.card_name) FROM local_cards c JOIN local_run r ON r.local_uuid = c.run_uuid "
                f"WHERE {where}", params).fetchone()[0]
            names = [r[0] for r in self._conn.execute(
                f"SELECT DISTINCT c.card_name FROM local_cards c JOIN local_run r ON r.local_uuid = c.run_uuid "
                f"WHERE {where} ORDER BY c.card_name COLLATE NOCASE LIMIT ? OFFSET ?", [*params, limit, offset])]
            items = []
            for name in names:
                rows = self._conn.execute(
                    "SELECT c.bin, COUNT(*) AS quantity, MIN(c.card_identifier) AS card_id "
                    "FROM local_cards c JOIN local_run r ON r.local_uuid = c.run_uuid "
                    "WHERE c.card_name = ? AND c.scan_status = 'identified' AND r.status NOT IN ('running', 'paused') "
                    "GROUP BY c.bin ORDER BY c.bin", (name,)).fetchall()
                items.append({
                    "id": rows[0]["card_id"] if rows else name,
                    "name": name,
                    "folders": [{"folder": r["bin"], "quantity": r["quantity"]} for r in rows if r["bin"]],
                })
        return items, total

    def export_csv(self) -> str:
        """Every card of every finished run, as RFC 4180 CSV (RF24 / RNF17)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.*, r.started_at AS run_started_at FROM local_cards c "
                "JOIN local_run r ON r.local_uuid = c.run_uuid "
                "WHERE r.status NOT IN ('running', 'paused') ORDER BY r.started_at, c.card_number").fetchall()
        out = io.StringIO()
        columns = ["run_uuid", "run_started_at", "card_number", "scan_status", "bin", "card_name", "set_code",
                   "collector_number", "color_identity", "type_line", "mana_cost", "rarity", "card_identifier",
                   "match_method", "confidence", "scanned_at"]
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(columns)
        for r in rows:
            writer.writerow(["" if r[c] is None else r[c] for c in columns])
        return out.getvalue()

    # ------------------------------------------------------- app tokens

    def issue_token(self, username: str, now: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute("INSERT INTO app_token VALUES (?, ?, ?)", (token, username, now))
        return token

    def token_user(self, token: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT username FROM app_token WHERE token = ?", (token,)).fetchone()
        return row[0] if row else None


def _run_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["run_id"] = d.pop("local_uuid")
    return d


def _card_dict(row: dict) -> dict:
    d = dict(row)
    d["card_id"] = d.pop("local_uuid")
    d.pop("run_uuid", None)
    return d
