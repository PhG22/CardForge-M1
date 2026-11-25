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
    color_counts = {g: sum(c["color"] == g for c in out_cards) for g in COLOR_GROUPS}
    return {"run": _run(row), "bin_counts": bin_counts, "scan_counts": scan_counts, "color_counts": color_counts,
            "cards": out_cards}


# The color filter of the collection and the export: the card's color identity, as the
# machine sorts it (RF20). Exactly one group per identified card.
COLOR_GROUPS = ("W", "U", "B", "R", "G", "colorless", "multicolor")

# Only the WUBRG letters count ("WU", "W,U" and "{W}{U}" are the same identity).
_COLORS = "upper(regexp_replace(coalesce(color_identity, ''), '[^WUBRGwubrg]', '', 'g'))"
COLOR_GROUP_SQL = (f"CASE WHEN length({_COLORS}) = 0 THEN 'colorless' "
                   f"WHEN length({_COLORS}) = 1 THEN {_COLORS} ELSE 'multicolor' END")


def run_exists(conn, user_id: int, run_id: int) -> bool:
    return conn.execute("SELECT 1 FROM runs WHERE id = %s AND started_by = %s",
                        (run_id, user_id)).fetchone() is not None


def _scope(user_id: int, run_id: int | None) -> tuple[str, dict]:
    """The user's cards, or only one of the user's runs (the caller checked it is theirs)."""
    return ("owner_id = %(user)s AND (%(run)s::bigint IS NULL OR run_id = %(run)s::bigint)",
            {"user": user_id, "run": run_id})


def collection(conn, user_id: int, query: str, limit: int, offset: int,
               run_id: int | None = None, color: str | None = None) -> dict:
    """The user's identified cards grouped by name, from every run or from one run (RF22 / RF24).
    `color` keeps one color group (COLOR_GROUPS). `color_counts` counts the copies per color group
    for the same scope and search, ignoring `color`, so the app can show them on its filter chips;
    `not_identified` counts the unidentified and face-down cards of the scope (0 while searching)."""
    scope, params = _scope(user_id, run_id)
    params = {**params, "q": query, "color": color}
    searched = f"{scope} AND (%(q)s = '' OR strpos(lower(card_name), lower(%(q)s)) > 0)"
    where = (f"{searched} AND scan_status = 'success' "
             f"AND (%(color)s::text IS NULL OR {COLOR_GROUP_SQL} = %(color)s::text)")
    total = conn.execute(f"SELECT count(DISTINCT card_name) AS n FROM cards WHERE {where}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT card_name, min(card_identifier) AS id, count(*) AS quantity, min(color_identity) AS color_identity,
               min({COLOR_GROUP_SQL}) AS color, min(type_line) AS type_line, min(mana_cost) AS mana_cost,
               min(rarity) AS rarity,
               string_agg(DISTINCT upper(set_code), ', ' ORDER BY upper(set_code)) AS sets
        FROM cards WHERE {where}
        GROUP BY card_name ORDER BY lower(card_name), card_name LIMIT %(limit)s OFFSET %(offset)s
        """, {**params, "limit": limit, "offset": offset}).fetchall()
    counts = {g: 0 for g in COLOR_GROUPS}
    for r in conn.execute(
            f"SELECT {COLOR_GROUP_SQL} AS color, count(*) AS n FROM cards "
            f"WHERE {searched} AND scan_status = 'success' GROUP BY 1", params):
        counts[r["color"]] = r["n"]
    not_identified = conn.execute(           # they have no name: a search never matches them
        f"SELECT count(*) AS n FROM cards WHERE {scope} AND scan_status <> 'success' AND %(q)s = ''",
        params).fetchone()["n"]
    items = [{"id": r["id"], "name": r["card_name"], "quantity": r["quantity"],
              "color_identity": r["color_identity"] or "", "color": r["color"], "type_line": r["type_line"],
              "mana_cost": r["mana_cost"], "rarity": r["rarity"], "sets": r["sets"] or ""} for r in rows]
    return {"items": items, "total": total, "color_counts": counts, "not_identified": not_identified}


def export_rows(conn, user_id: int, run_id: int | None = None) -> list[dict]:
    """Every card the user owns (or only one run's), one row per (print, status) with its quantity.
    Unidentified and face-down cards are included (status column), after the identified ones.
    The app turns these rows into the CSV or TXT file (RF24 / RNF17)."""
    scope, params = _scope(user_id, run_id)
    rows = conn.execute(
        f"""
        SELECT count(*) AS quantity, card_name, set_code, collector_number, rarity, color_identity, type_line,
               mana_cost, card_identifier,
               CASE WHEN scan_status = 'success' THEN {COLOR_GROUP_SQL} END AS color,
               CASE WHEN scan_status = 'success' THEN 'identified' ELSE coalesce(flag::text, 'unidentified') END
                   AS status
        FROM cards WHERE {scope}
        GROUP BY card_name, set_code, collector_number, rarity, color_identity, type_line, mana_cost,
                 card_identifier, scan_status, flag
        ORDER BY (scan_status <> 'success'), lower(card_name), set_code, collector_number, flag
        """, params).fetchall()
    return [{"quantity": r["quantity"], "name": r["card_name"], "set_code": r["set_code"],
             "collector_number": r["collector_number"], "rarity": r["rarity"],
             "color_identity": r["color_identity"], "color": r["color"], "type_line": r["type_line"],
             "mana_cost": r["mana_cost"], "status": r["status"], "scryfall_id": r["card_identifier"]} for r in rows]


# ------------------------------------------------------------- notifications (RF38)

NOTIFICATION_COLUMNS = ("id, run_local_uuid, kind, code, status, message, cards_processed, event_time, "
                        "created_at")
NOTIFICATION_RETENTION_DAYS = 30


def insert_notification(conn, user_id: int, event: dict) -> tuple[int, bool]:
    """Store one alert of a run for its owner. A resent alert (same event_id) is stored once.
    Returns (id, created)."""
    row = conn.execute(
        """
        INSERT INTO notifications (user_id, event_id, run_local_uuid, kind, code, status, message,
                                   cards_processed, event_time)
        VALUES (%(user_id)s, %(event_id)s, %(run_id)s, %(kind)s, %(code)s, %(status)s, %(message)s,
                %(cards_processed)s, %(time)s)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING id
        """, {**event, "user_id": user_id}).fetchone()
    if row is not None:
        conn.execute("DELETE FROM notifications WHERE user_id = %s AND created_at < now() - make_interval(days => %s)",
                     (user_id, NOTIFICATION_RETENTION_DAYS))
        return row["id"], True
    existing = conn.execute("SELECT id FROM notifications WHERE event_id = %s", (event["event_id"],)).fetchone()
    return existing["id"], False


def notifications_after(conn, user_id: int, after: int, limit: int, run_id=None) -> list[dict]:
    """The user's alerts with an id above `after`, oldest first; only one run's when run_id is given."""
    rows = conn.execute(
        f"SELECT {NOTIFICATION_COLUMNS} FROM notifications WHERE user_id = %s AND id > %s "
        "AND (%s::uuid IS NULL OR run_local_uuid = %s::uuid) ORDER BY id LIMIT %s",
        (user_id, after, run_id, run_id, limit)).fetchall()
    return [_notification(r) for r in rows]


def last_notification_id(conn, user_id: int) -> int:
    row = conn.execute("SELECT coalesce(max(id), 0) AS id FROM notifications WHERE user_id = %s",
                       (user_id,)).fetchone()
    return row["id"]


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


def _notification(r: dict) -> dict:
    return {"id": r["id"], "run_id": str(r["run_local_uuid"]) if r["run_local_uuid"] else None,
            "kind": r["kind"], "code": r["code"], "status": r["status"], "message": r["message"],
            "cards_processed": r["cards_processed"], "time": iso(r["event_time"]),
            "received_at": iso(r["created_at"])}


def color_group(color_identity: str | None) -> str:
    """Python twin of COLOR_GROUP_SQL."""
    colors = {ch for ch in (color_identity or "").upper() if ch in "WUBRG"}
    if not colors:
        return "colorless"
    return next(iter(colors)) if len(colors) == 1 else "multicolor"


def _card(c: dict) -> dict:
    status = "identified" if c["scan_status"] == "success" else (c["flag"] or "unidentified")
    return {"card_number": c["card_number"], "status": status, "bin": c["bin"],
            "color": color_group(c["color_identity"]) if status == "identified" else None,
            "card_identifier": c["card_identifier"], "card_name": c["card_name"], "set_code": c["set_code"],
            "collector_number": c["collector_number"], "color_identity": c["color_identity"],
            "type_line": c["type_line"], "mana_cost": c["mana_cost"], "rarity": c["rarity"],
            "match_method": c["match_method"], "confidence": c["confidence"], "attempts": c["attempts"],
            "scanned_at": iso(c["scanned_at"])}
