"""Local run storage on the Pi: run.db (SQLite), tables local_run and local_cards (DB-ER).

* RF19 / RNF20: one record per card that physically left the platform, written in
  one transaction together with the run counters, right after the drop.
* RNF19: WAL journal + synchronous=FULL, so a committed card survives a power cut.
* Runs that were still running or paused when the Pi lost power are marked
  failed (INTERRUPTED) on the next start.
* Owner: the user who pressed Start in the app (started_by = PostgreSQL users.id,
  checked with the server at start). Every card of the run belongs to that user.
* Sync (DB02 / DB04): once a run is finished, sync.SyncWorker uploads it with its
  cards to the CardForge server, which stores them in PostgreSQL. sync_state goes
  pending -> synced, or failed (retried with backoff, next_sync_at). Runs without an
  owner (no server configured when they ran) stay on the Pi only.

The machine-local results (RF22) and inventory (RF24) are also read from here.
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
from typing import Callable

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
    sync_attempts    INTEGER NOT NULL DEFAULT 0,
    started_by       INTEGER,                   -- PostgreSQL users.id; NULL = no server: never uploaded
    owner_name       TEXT,                      -- that user's username (logs, /sync/status)
    remote_run_id    INTEGER,                   -- PostgreSQL runs.id once synced
    last_sync_error  TEXT,
    synced_at        TEXT,
    next_sync_at     TEXT                       -- after a failed upload: not before this time
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
    owner_id         INTEGER,                   -- = local_run.started_by (PostgreSQL users.id)
    UNIQUE (run_uuid, card_number)
);
CREATE INDEX IF NOT EXISTS idx_local_cards_name ON local_cards(card_name);

CREATE TABLE IF NOT EXISTS app_token (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Columns added after the first version of run.db: added in place to an existing file.
SCHEMA_VERSION = 2
ADDED_COLUMNS = {
    "local_run": {"started_by": "INTEGER", "owner_name": "TEXT", "remote_run_id": "INTEGER",
                  "last_sync_error": "TEXT", "synced_at": "TEXT", "next_sync_at": "TEXT"},
    "local_cards": {"owner_id": "INTEGER"},
}
# Indexes on those columns: created after the columns exist.
LATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_local_run_sync ON local_run(sync_state, next_sync_at)
    WHERE sync_state <> 'synced' AND started_by IS NOT NULL;
"""

ACTIVE_STATUSES = ("running", "paused")
FINISHED = "status NOT IN ('running', 'paused')"
SYNC_CARD_COLUMNS = ("card_number", "scan_status", "bin", "card_identifier", "card_name", "set_code",
                     "collector_number", "color_identity", "type_line", "mana_cost", "rarity", "match_method",
                     "confidence", "attempts", "scanned_at", "error_message")
CARD_COLUMNS = ("card_identifier", "scan_status", "bin", "match_method", "confidence", "attempts",
                "card_name", "set_code", "collector_number", "color_identity", "type_line", "mana_cost",
                "rarity", "error_message")


@dataclass(frozen=True)
class Owner:
    """The signed-in app user who started a run (PostgreSQL users.id)."""
    user_id: int
    username: str


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
        self._migrate()

    def _migrate(self) -> None:
        """Bring a run.db made by an older version up to SCHEMA_VERSION (adds columns, keeps the data)."""
        if self._conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        for table, columns in ADDED_COLUMNS.items():
            existing = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, sql_type in columns.items():
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        self._conn.executescript(LATE_INDEXES)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

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

    def create_run(self, run_id: str, started_at: str, owner: Owner | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO local_run (local_uuid, status, started_at, started_by, owner_name) "
                "VALUES (?, 'running', ?, ?, ?)",
                (run_id, started_at, owner.user_id if owner else None, owner.username if owner else None))

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
                    f"INSERT INTO local_cards (local_uuid, run_uuid, card_number, scanned_at, owner_id, "
                    f"{', '.join(CARD_COLUMNS)}) "
                    f"VALUES (?, ?, ?, ?, (SELECT started_by FROM local_run WHERE local_uuid = ?), "
                    f"{', '.join('?' * len(CARD_COLUMNS))})",
                    (card_uuid, run_id, card_number, scanned_at, run_id, *values))
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

    # ------------------------------------------------------ server sync

    def runs_to_sync(self, now: str, limit: int = 20) -> list[str]:
        """Finished runs with an owner, not uploaded yet, whose retry time has come (oldest first)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT local_uuid FROM local_run WHERE {FINISHED} AND sync_state <> 'synced' "
                "AND started_by IS NOT NULL AND (next_sync_at IS NULL OR next_sync_at <= ?) "
                "ORDER BY started_at LIMIT ?", (now, limit)).fetchall()
        return [r[0] for r in rows]

    def sync_payload(self, run_id: str) -> dict | None:
        """Body of the server's POST /sync/runs: the run and all its cards."""
        with self._lock:
            run = self._conn.execute(f"SELECT * FROM local_run WHERE local_uuid = ? AND {FINISHED}",
                                     (run_id,)).fetchone()
            if run is None or run["started_by"] is None:
                return None
            cards = self._conn.execute(
                "SELECT * FROM local_cards WHERE run_uuid = ? ORDER BY card_number", (run_id,)).fetchall()
        return {
            "run": {k: run[k] for k in ("local_uuid", "started_by", "status", "started_at", "stopped_at",
                                        "cards_processed", "successful_scans", "failed_scans", "error_code",
                                        "message")},
            "cards": [{"local_uuid": c["local_uuid"],
                       "owner_id": c["owner_id"] if c["owner_id"] is not None else run["started_by"],
                       **{k: c[k] for k in SYNC_CARD_COLUMNS}} for c in cards],
        }

    def mark_synced(self, run_id: str, remote_run_id: int, now: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE local_run SET sync_state = 'synced', remote_run_id = ?, synced_at = ?, "
                "last_sync_error = NULL, next_sync_at = NULL WHERE local_uuid = ?", (remote_run_id, now, run_id))

    def mark_sync_failed(self, run_id: str, error: str, next_attempt: Callable[[int], str]) -> int:
        """Count one more failed upload; next_attempt(attempts) gives the time of the next try.
        Returns how many uploads of this run have failed so far."""
        with self._lock:
            row = self._conn.execute("SELECT sync_attempts FROM local_run WHERE local_uuid = ?", (run_id,)).fetchone()
            if row is None:
                return 0
            attempts = row[0] + 1
            self._conn.execute(
                "UPDATE local_run SET sync_state = 'failed', sync_attempts = ?, last_sync_error = ?, "
                "next_sync_at = ? WHERE local_uuid = ?", (attempts, error[:500], next_attempt(attempts), run_id))
        return attempts

    def clear_sync_backoff(self) -> None:
        """Make every run waiting for a retry due now (POST /sync/now)."""
        with self._lock:
            self._conn.execute("UPDATE local_run SET next_sync_at = NULL WHERE sync_state = 'failed'")

    def sync_status(self) -> dict:
        """How many finished runs are uploaded, waiting, failing, or kept on the Pi only."""
        with self._lock:
            counts = {r[0]: r[1] for r in self._conn.execute(
                f"SELECT CASE WHEN started_by IS NULL THEN 'local_only' ELSE sync_state END, COUNT(*) "
                f"FROM local_run WHERE {FINISHED} GROUP BY 1")}
            last = self._conn.execute(
                "SELECT local_uuid, last_sync_error, next_sync_at, sync_attempts FROM local_run "
                "WHERE sync_state = 'failed' AND started_by IS NOT NULL ORDER BY started_at DESC LIMIT 1").fetchone()
        return {
            "synced": counts.get("synced", 0), "pending": counts.get("pending", 0),
            "failed": counts.get("failed", 0), "local_only": counts.get("local_only", 0),
            "last_error": dict(last) if last else None,
        }

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
