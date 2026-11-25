"""CardForge server on a real PostgreSQL: accounts, run upload, results, collection, export.
Skipped without CF_TEST_DATABASE_URL (see conftest.py)."""

import uuid

import pytest

from conftest import MACHINE_TOKEN

psycopg = pytest.importorskip("psycopg", reason="server dependencies: pip install -r cardforge_server/requirements.txt")

MACHINE = {"Authorization": f"Bearer {MACHINE_TOKEN}"}


def signup(client, username="luca", email=None, password="correct-horse"):
    r = client.post("/auth/signup", json={"username": username, "email": email or f"{username}@example.com",
                                          "password": password})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]


def card(owner_id, number, status="identified", name=None, bin_=None, set_code="FIN", collector="1",
         colors="W"):
    identified = status == "identified"
    return {"local_uuid": str(uuid.uuid4()), "owner_id": owner_id, "card_number": number, "scan_status": status,
            "bin": bin_ if bin_ is not None else (1 if identified else 6),
            "card_identifier": f"print-{name}" if identified else None,
            "card_name": name if identified else None, "set_code": set_code if identified else None,
            "collector_number": collector if identified else None, "color_identity": colors if identified else None,
            "type_line": "Basic Land" if identified else None, "rarity": "common" if identified else None,
            "attempts": 1, "confidence": 100 if identified else 0,
            "scanned_at": f"2026-11-11T20:00:{number:02d}.000Z",
            "error_message": None if identified else "low_confidence after 3 capture(s)"}


def run_body(owner_id, cards, status="completed", started="2026-11-11T20:00:00.000Z"):
    ok = sum(c["scan_status"] == "identified" for c in cards)
    return {"run": {"local_uuid": str(uuid.uuid4()), "started_by": owner_id, "status": status,
                    "started_at": started, "stopped_at": "2026-11-11T20:10:00.000Z",
                    "cards_processed": len(cards), "successful_scans": ok, "failed_scans": len(cards) - ok,
                    "error_code": None, "message": f"Stack is empty: {len(cards)} card(s) processed."},
            "cards": cards}


# ------------------------------------------------------------------ accounts

def test_signup_signs_the_user_in(server):
    headers, user = signup(server)
    assert user["username"] == "luca" and user["email"] == "luca@example.com"
    me = server.get("/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["id"] == user["id"]


@pytest.mark.parametrize("body, code", [
    ({"username": "ab", "email": "a@example.com", "password": "long-enough"}, "INVALID_USERNAME"),
    ({"username": "has space", "email": "a@example.com", "password": "long-enough"}, "INVALID_USERNAME"),
    ({"username": "me@home", "email": "a@example.com", "password": "long-enough"}, "INVALID_USERNAME"),
    ({"username": "alice", "email": "not-an-email", "password": "long-enough"}, "INVALID_EMAIL"),
    ({"username": "alice", "email": "a@example.com", "password": "short"}, "WEAK_PASSWORD"),
])
def test_signup_validates_the_fields(server, body, code):
    r = server.post("/auth/signup", json=body)
    assert r.status_code == 422 and r.json()["code"] == code


def test_signup_refuses_taken_username_or_email_ignoring_case(server):
    signup(server, "Luca", "luca@example.com")
    r = server.post("/auth/signup", json={"username": "LUCA", "email": "other@example.com", "password": "x" * 8})
    assert r.status_code == 409 and r.json()["code"] == "USERNAME_TAKEN" and r.json()["field"] == "username"
    r = server.post("/auth/signup", json={"username": "other", "email": "LUCA@example.COM", "password": "x" * 8})
    assert r.status_code == 409 and r.json()["code"] == "EMAIL_TAKEN"


def test_password_is_stored_hashed(server, pg_url):
    signup(server, password="correct-horse")
    with psycopg.connect(pg_url) as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("$argon2id$") and "correct-horse" not in stored


def test_login_with_username_or_email(server):
    signup(server, "luca", "luca@example.com", "correct-horse")
    for identifier in ("luca", "LUCA", "Luca@Example.com"):
        r = server.post("/auth/login", json={"identifier": identifier, "password": "correct-horse"})
        assert r.status_code == 200, identifier
        assert server.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    for identifier, password in (("luca", "wrong-password"), ("nobody", "correct-horse")):
        r = server.post("/auth/login", json={"identifier": identifier, "password": password})
        assert r.status_code == 401 and r.json()["code"] == "INVALID_CREDENTIALS"


def test_disabled_account_cannot_sign_in_or_use_its_token(server, pg_url):
    headers, _ = signup(server, password="correct-horse")
    with psycopg.connect(pg_url) as conn:
        conn.execute("UPDATE users SET is_active = false")
    assert server.post("/auth/login", json={"identifier": "luca", "password": "correct-horse"}).status_code == 403
    assert server.get("/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize("path", ["/auth/me", "/runs", "/runs/1", "/cards", "/cards/export"])
def test_user_routes_need_a_valid_token(server, path):
    assert server.get(path).status_code == 401
    assert server.get(path, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


# ------------------------------------------------------------------ run upload

def test_upload_needs_a_machine_token(server):
    _, user = signup(server)
    body = run_body(user["id"], [card(user["id"], 1, name="Plains")])
    assert server.post("/sync/runs", json=body).status_code == 401
    assert server.post("/sync/runs", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_upload_is_idempotent(server, pg_url):
    headers, user = signup(server)
    body = run_body(user["id"], [card(user["id"], 1, name="Plains"), card(user["id"], 2, status="face_down")])
    first = server.post("/sync/runs", json=body, headers=MACHINE)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True and first.json()["cards_inserted"] == 2
    again = server.post("/sync/runs", json=body, headers=MACHINE)          # e.g. the reply was lost
    assert again.status_code == 200
    assert again.json() == {**first.json(), "created": False, "cards_inserted": 0}
    with psycopg.connect(pg_url) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM cards").fetchone()[0] == 2
        flags = conn.execute("SELECT scan_status::text, flag::text FROM cards ORDER BY card_number").fetchall()
    assert flags == [("success", None), ("failed", "face_down")]


def test_upload_with_counters_that_do_not_match_the_cards_is_rejected(server, pg_url):
    _, user = signup(server)
    body = run_body(user["id"], [card(user["id"], 1, name="Plains")])
    body["run"]["cards_processed"] = 2
    r = server.post("/sync/runs", json=body, headers=MACHINE)
    assert r.status_code == 422 and r.json()["code"] == "INCONSISTENT_RUN"
    with psycopg.connect(pg_url) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_upload_of_a_run_in_progress_or_for_an_unknown_user_is_rejected(server):
    _, user = signup(server)
    body = run_body(user["id"], [card(user["id"], 1, name="Plains")])
    body["run"]["status"] = "running"
    assert server.post("/sync/runs", json=body, headers=MACHINE).status_code == 422
    body = run_body(999, [card(999, 1, name="Plains")])
    r = server.post("/sync/runs", json=body, headers=MACHINE)
    assert r.status_code == 422 and r.json()["code"] == "UNKNOWN_USER"


def test_a_retry_cannot_move_a_run_to_another_user(server):
    _, alice = signup(server, "alice")
    _, bob = signup(server, "bob")
    body = run_body(alice["id"], [card(alice["id"], 1, name="Plains")])
    assert server.post("/sync/runs", json=body, headers=MACHINE).status_code == 200
    body["run"]["started_by"] = bob["id"]
    body["cards"][0]["owner_id"] = bob["id"]
    r = server.post("/sync/runs", json=body, headers=MACHINE)
    assert r.status_code == 409 and r.json()["code"] == "RUN_OWNER_MISMATCH"


# ------------------------------------------------------------------ results, collection, export

def test_each_user_sees_only_their_runs(server):
    alice_h, alice = signup(server, "alice")
    bob_h, bob = signup(server, "bob")
    server.post("/sync/runs", json=run_body(alice["id"], [card(alice["id"], 1, name="Plains")],
                                            started="2026-11-10T10:00:00.000Z"), headers=MACHINE)
    server.post("/sync/runs", json=run_body(alice["id"], [card(alice["id"], 1, name="Island", bin_=2)],
                                            status="cancelled"), headers=MACHINE)
    server.post("/sync/runs", json=run_body(bob["id"], [card(bob["id"], 1, name="Swamp", bin_=3)]), headers=MACHINE)

    runs = server.get("/runs", headers=alice_h).json()
    assert runs["total"] == 2
    assert [r["status"] for r in runs["runs"]] == ["cancelled", "completed"]       # newest first
    bob_run = server.get("/runs", headers=bob_h).json()["runs"][0]["run_id"]
    assert server.get(f"/runs/{bob_run}", headers=alice_h).status_code == 404


def test_run_results_have_tray_counts_and_every_card(server):
    headers, user = signup(server)
    cards = [card(user["id"], 1, name="Plains"), card(user["id"], 2, name="Forest", bin_=5),
             card(user["id"], 3, status="unidentified"), card(user["id"], 4, status="face_down")]
    run_id = server.post("/sync/runs", json=run_body(user["id"], cards), headers=MACHINE).json()["run_id"]
    results = server.get(f"/runs/{run_id}", headers=headers).json()
    assert results["run"]["cards_processed"] == 4
    assert results["bin_counts"] == {"1": 1, "5": 1, "6": 2}
    assert results["scan_counts"] == {"identified": 2, "unidentified": 1, "face_down": 1}
    assert results["color_counts"] == {"W": 2, "U": 0, "B": 0, "R": 0, "G": 0, "colorless": 0, "multicolor": 0}
    assert [c["status"] for c in results["cards"]] == ["identified", "identified", "unidentified", "face_down"]
    assert [c["color"] for c in results["cards"]] == ["W", "W", None, None]
    assert results["cards"][1]["card_name"] == "Forest"


def _two_runs(server):
    """Run 1: 2 Lightning Bolt (R), Opt (U), Lightning Helix (RW), Sol Ring (colorless), 1 unidentified.
    Run 2: 1 Lightning Bolt."""
    headers, user = signup(server)
    uid = user["id"]
    first = server.post("/sync/runs", json=run_body(uid, [
        card(uid, 1, name="Lightning Bolt", bin_=4, colors="R"),
        card(uid, 2, name="Lightning Bolt", bin_=4, colors="R"),
        card(uid, 3, name="Opt", bin_=2, colors="U", set_code="ELD", collector="59"),
        card(uid, 4, name="Lightning Helix", bin_=6, colors="RW"),
        card(uid, 5, name="Sol Ring", bin_=6, colors=""),
        card(uid, 6, status="unidentified")]), headers=MACHINE).json()["run_id"]
    second = server.post("/sync/runs", json=run_body(uid, [
        card(uid, 1, name="Lightning Bolt", bin_=6, colors="R", set_code="M10", collector="146")]),
        headers=MACHINE).json()["run_id"]
    return headers, first, second


def test_collection_groups_cards_by_name_across_all_runs(server):
    headers, _, _ = _two_runs(server)
    page = server.get("/cards", headers=headers).json()
    assert page["total"] == 4                                   # unidentified cards are not in the collection
    assert [(i["name"], i["quantity"], i["color"]) for i in page["items"]] == [
        ("Lightning Bolt", 3, "R"), ("Lightning Helix", 1, "multicolor"), ("Opt", 1, "U"),
        ("Sol Ring", 1, "colorless")]
    bolt = page["items"][0]
    assert "folders" not in bolt and bolt["sets"] == "FIN, M10" and bolt["color_identity"] == "R"
    assert page["color_counts"] == {"W": 0, "U": 1, "B": 0, "R": 3, "G": 0, "colorless": 1, "multicolor": 1}
    assert page["not_identified"] == 1
    found = server.get("/cards", params={"query": "BOLT"}, headers=headers).json()
    assert [i["name"] for i in found["items"]] == ["Lightning Bolt"] and found["total"] == 1
    assert found["color_counts"]["R"] == 3 and found["color_counts"]["U"] == 0
    assert found["not_identified"] == 0
    second = server.get("/cards", params={"limit": 1, "offset": 1}, headers=headers).json()
    assert [i["name"] for i in second["items"]] == ["Lightning Helix"] and second["total"] == 4


@pytest.mark.parametrize("color, names", [
    ("R", ["Lightning Bolt"]), ("r", ["Lightning Bolt"]), ("U", ["Opt"]), ("W", []),
    ("colorless", ["Sol Ring"]), ("Multicolor", ["Lightning Helix"]),
])
def test_collection_filters_by_color_group(server, color, names):
    headers, _, _ = _two_runs(server)
    page = server.get("/cards", params={"color": color}, headers=headers).json()
    assert [i["name"] for i in page["items"]] == names and page["total"] == len(names)
    # the chip counts ignore the color filter, so every chip keeps its number
    assert page["color_counts"]["R"] == 3


def test_collection_of_one_run(server):
    headers, first, second = _two_runs(server)
    page = server.get("/cards", params={"run_id": second}, headers=headers).json()
    assert [(i["name"], i["quantity"]) for i in page["items"]] == [("Lightning Bolt", 1)]
    assert page["not_identified"] == 0
    page = server.get("/cards", params={"run_id": first, "color": "R"}, headers=headers).json()
    assert [(i["name"], i["quantity"]) for i in page["items"]] == [("Lightning Bolt", 2)]
    assert page["not_identified"] == 1


def test_collection_refuses_a_bad_color_or_someone_elses_run(server):
    headers, first, _ = _two_runs(server)
    r = server.get("/cards", params={"color": "purple"}, headers=headers)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_COLOR"
    other, _ = signup(server, "other")
    for path in ("/cards", "/cards/export"):
        r = server.get(path, params={"run_id": first}, headers=other)
        assert r.status_code == 404 and r.json()["code"] == "RUN_NOT_FOUND"


def test_export_lists_every_card_with_its_quantity(server):
    headers, user = signup(server)
    uid = user["id"]
    server.post("/sync/runs", json=run_body(uid, [card(uid, 1, name="Plains"), card(uid, 2, name="Plains", bin_=6),
                                                  card(uid, 3, name="Opt", bin_=2, set_code="ELD", collector="59",
                                                       colors="U"),
                                                  card(uid, 4, status="face_down")]), headers=MACHINE)
    export = server.get("/cards/export", headers=headers).json()
    assert export["total_cards"] == 4 and export["run_id"] is None
    # one row per print: the tray a copy fell into does not split it
    rows = [(r["quantity"], r["name"], r["set_code"], r["color"], r["status"]) for r in export["rows"]]
    assert rows == [(1, "Opt", "ELD", "U", "identified"), (2, "Plains", "FIN", "W", "identified"),
                    (1, None, None, None, "face_down")]
    assert all("folder" not in r for r in export["rows"])


def test_export_of_one_run(server):
    headers, first, second = _two_runs(server)
    export = server.get("/cards/export", params={"run_id": second}, headers=headers).json()
    assert export["total_cards"] == 1 and export["run_id"] == second
    assert [(r["quantity"], r["name"], r["set_code"]) for r in export["rows"]] == [(1, "Lightning Bolt", "M10")]
    whole = server.get("/cards/export", headers=headers).json()
    assert whole["total_cards"] == 7
