"""SQL for users, runs and cards. Every function takes an open connection (dict rows);
the caller owns the transaction."""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb


class UniqueTaken(Exception):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field              # "username" or "email"


# ------------------------------------------------------------------ users

USER_COLUMNS = "id, username, email, is_active, created_at, last_login_at"


def create_user(conn, username: str, email: str, password_hash: str) -> dict:
    try:
        with conn.transaction():        # savepoint: a duplicate does not poison the outer transaction
            return conn.execute(
                f"INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING {USER_COLUMNS}",
                (username, email, password_hash)).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        constraint = exc.diag.constraint_name or ""
        raise UniqueTaken("email" if "email" in constraint else "username") from exc


def find_login(conn, identifier: str) -> dict | None:
    """By email when the identifier has an @, else by username (both case-insensitive)."""
    column = "email" if "@" in identifier else "username"
    return conn.execute(
        f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE lower({column}) = lower(%s)",
        (identifier,)).fetchone()


def get_user(conn, user_id: int) -> dict | None:
    return conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,)).fetchone()


def record_login(conn, user_id: int, new_hash: str | None = None) -> None:
    if new_hash:
        conn.execute("UPDATE users SET last_login_at = now(), password_hash = %s WHERE id = %s", (new_hash, user_id))
    else:
        conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,))


def public_user(row: dict) -> dict:
    return {"id": row["id"], "username": row["username"], "email": row["email"],
            "created_at": iso(row["created_at"]), "last_login_at": iso(row["last_login_at"])}


# ------------------------------------------------------------------ sync (DB04)

def upsert_run(conn, run: dict) -> dict:
    """sync_run.sql step 1. DO UPDATE (not DO NOTHING) so RETURNING gives the id on a retry too.
    The owner (started_by) is never changed by a retry."""
    return conn.execute(
        """
        INSERT INTO runs (local_uuid, started_by, status, started_at, stopped_at, cards_processed,
                          successful_scans, failed_scans, error_code, message, synced_at)
        VALUES (%(local_uuid)s, %(started_by)s, %(status)s, %(started_at)s, %(stopped_at)s, %(cards_processed)s,
                %(successful_scans)s, %(failed_scans)s, %(error_code)s, %(message)s, now())
        ON CONFLICT (local_uuid) DO UPDATE
            SET status           = EXCLUDED.status,
                stopped_at       = EXCLUDED.stopped_at,
                cards_processed  = EXCLUDED.cards_processed,
                successful_scans = EXCLUDED.successful_scans,
                failed_scans     = EXCLUDED.failed_scans,
                error_code       = EXCLUDED.error_code,
                message          = EXCLUDED.message,
                synced_at        = now()
        RETURNING id, started_by, (xmax = 0) AS created
        """, run).fetchone()


def insert_cards(conn, run_id: int, cards: list[dict]) -> int:
    """sync_run.sql step 2: every card of the run in one statement. Cards already
    uploaded (same local_uuid) are skipped. Returns how many rows were inserted."""
    if not cards:
        return 0
    cur = conn.execute(
        """
        INSERT INTO cards (local_uuid, run_id, owner_id, card_number, card_identifier, scan_status, flag, bin,
                           card_name, set_code, collector_number, color_identity, type_line, mana_cost, rarity,
                           match_method, confidence, attempts, scanned_at, error_message, created_at)
        SELECT c.local_uuid, %(run_id)s, c.owner_id, c.card_number, c.card_identifier,
               c.scan_status::scan_status, c.flag::scan_flag, c.bin,
               c.card_name, c.set_code, c.collector_number, c.color_identity, c.type_line, c.mana_cost, c.rarity,
               c.match_method, c.confidence, c.attempts, c.scanned_at, c.error_message, c.scanned_at
        FROM jsonb_to_recordset(%(cards)s) AS c(
               local_uuid uuid, owner_id bigint, card_number integer, card_identifier text, scan_status text,
               flag text, bin smallint, card_name text, set_code text, collector_number text,
               color_identity text, type_line text, mana_cost text, rarity text, match_method text,
               confidence integer, attempts integer, scanned_at timestamptz, error_message text)
        ON CONFLICT (local_uuid) DO NOTHING
        """, {"run_id": run_id, "cards": Jsonb(cards)})
    return cur.rowcount


# ------------------------------------------------------------- results (DB05)

RUN_COLUMNS = ("id, local_uuid, status, started_at, stopped_at, cards_processed, successful_scans, "
               "failed_scans, error_code, message, synced_at")


def list_runs(conn, user_id: int, limit: int, offset: int) -> tuple[list[dict], int]:
    total = conn.execute("SELECT count(*) AS n FROM runs WHERE started_by = %s", (user_id,)).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {RUN_COLUMNS} FROM runs WHERE started_by = %s ORDER BY started_at DESC, id DESC "
        "LIMIT %s OFFSET %s", (user_id, limit, offset)).fetchall()
    return [_run(r) for r in rows], total


def run_results(conn, user_id: int, run_id: int) -> dict | None:
    """Per-tray counts and every card of one of the user's runs (RF22). None = not the user's run."""
    row = conn.execute(f"SELECT {RUN_COLUMNS} FROM runs WHERE id = %s AND started_by = %s",
                       (run_id, user_id)).fetchone()
    if row is None:
        return None
    cards = conn.execute(
        """
        SELECT card_number, scan_status, flag, bin, card_identifier, card_name, set_code, collector_number,
               color_identity, type_line, mana_cost, rarity, match_method, confidence, attempts, scanned_at
        FROM cards WHERE run_id = %s ORDER BY card_number NULLS LAST, id
        """, (run_id,)).fetchall()
    out_cards = [_card(c) for c in cards]
    bin_counts: dict[str, int] = {}
    for c in out_cards:
        key = str(c["bin"]) if c["bin"] is not None else "none"
        bin_counts[key] = bin_counts.get(key, 0) + 1
    scan_counts = {s: sum(c["status"] == s for c in out_cards) for s in ("identified", "unidentified", "face_down")}
    return {"run": _run(row), "bin_counts": bin_counts, "scan_counts": scan_counts, "cards": out_cards}


def collection(conn, user_id: int, query: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """The user's identified cards grouped by name, with the copies per tray (the app's
    "Cards in folders" contract). An empty query lists every card."""
    params = {"user": user_id, "q": query}
    where = ("owner_id = %(user)s AND scan_status = 'success' "
             "AND (%(q)s = '' OR strpos(lower(card_name), lower(%(q)s)) > 0)")
    total = conn.execute(f"SELECT count(DISTINCT card_name) AS n FROM cards WHERE {where}", params).fetchone()["n"]
    names = conn.execute(
        f"SELECT card_name, min(card_identifier) AS id, count(*) AS quantity FROM cards WHERE {where} "
        "GROUP BY card_name ORDER BY lower(card_name), card_name LIMIT %(limit)s OFFSET %(offset)s",
        {**params, "limit": limit, "offset": offset}).fetchall()
    folders: dict[str, list[dict]] = {}
    if names:
        for r in conn.execute(
                "SELECT card_name, bin, count(*) AS quantity FROM cards "
                "WHERE owner_id = %s AND scan_status = 'success' AND bin IS NOT NULL AND card_name = ANY(%s) "
                "GROUP BY card_name, bin ORDER BY bin", (user_id, [n["card_name"] for n in names])):
            folders.setdefault(r["card_name"], []).append({"folder": r["bin"], "quantity": r["quantity"]})
    items = [{"id": n["id"], "name": n["card_name"], "quantity": n["quantity"],
              "folders": folders.get(n["card_name"], [])} for n in names]
    return items, total


def export_rows(conn, user_id: int) -> list[dict]:
    """Every card the user owns, one row per (print, tray, status) with its quantity.
    Unidentified and face-down cards are included (status column), after the identified ones.
    The app turns these rows into the CSV file (RF24 / RNF17)."""
    rows = conn.execute(
        """
        SELECT count(*) AS quantity, card_name, set_code, collector_number, rarity, color_identity, type_line,
               mana_cost, bin, card_identifier,
               CASE WHEN scan_status = 'success' THEN 'identified' ELSE coalesce(flag::text, 'unidentified') END
                   AS status
        FROM cards WHERE owner_id = %s
        GROUP BY card_name, set_code, collector_number, rarity, color_identity, type_line, mana_cost, bin,
                 card_identifier, scan_status, flag
        ORDER BY (scan_status <> 'success'), lower(card_name), set_code, collector_number, bin, flag
        """, (user_id,)).fetchall()
    return [{"quantity": r["quantity"], "name": r["card_name"], "set_code": r["set_code"],
             "collector_number": r["collector_number"], "rarity": r["rarity"],
             "color_identity": r["color_identity"], "type_line": r["type_line"], "mana_cost": r["mana_cost"],
             "folder": r["bin"], "status": r["status"], "scryfall_id": r["card_identifier"]} for r in rows]


# ------------------------------------------------------------------ helpers

def iso(value: datetime | None) -> str | None:
    """Same format as the Pi: 2026-10-21T22:19:00.123Z"""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run(r: dict) -> dict:
    return {"run_id": r["id"], "local_uuid": str(r["local_uuid"]) if r["local_uuid"] else None,
            "status": r["status"], "started_at": iso(r["started_at"]), "stopped_at": iso(r["stopped_at"]),
            "cards_processed": r["cards_processed"], "successful_scans": r["successful_scans"],
            "failed_scans": r["failed_scans"], "error_code": r["error_code"], "message": r["message"],
            "synced_at": iso(r["synced_at"])}


def _card(c: dict) -> dict:
    status = "identified" if c["scan_status"] == "success" else (c["flag"] or "unidentified")
    return {"card_number": c["card_number"], "status": status, "bin": c["bin"],
            "card_identifier": c["card_identifier"], "card_name": c["card_name"], "set_code": c["set_code"],
            "collector_number": c["collector_number"], "color_identity": c["color_identity"],
            "type_line": c["type_line"], "mana_cost": c["mana_cost"], "rarity": c["rarity"],
            "match_method": c["match_method"], "confidence": c["confidence"], "attempts": c["attempts"],
            "scanned_at": iso(c["scanned_at"])}
