# CardForge server (accounts, PostgreSQL, run upload)

`cardforge_server/` is the server of the design (Deliverable 3, DB01–DB08): it owns the
PostgreSQL database, the user accounts, and every finished run with its cards. The app
talks to it for sign-up, sign-in, the run history, the collection and the CSV export;
the Raspberry Pi talks to it to check who started a run, to upload the run when it ends, and
to send the alerts of a run (a fault that paused it, its end) that the app shows as
notifications with the app closed (RF38).

```
App ──HTTPS──► CardForge server ──► PostgreSQL (users, runs, cards, notifications)
 │   signup/login, /runs, /cards, /cards/export        ▲
 │   background service: GET /notifications?wait=25   │ POST /sync/runs (machine token)
 │                                                      │ POST /machine/events (machine token, RF38)
 └──Wi-Fi──► Raspberry Pi (sorter_api) ── run.db (SQLite: local_run, local_cards) ──┘
     POST /run/start + the user's token  ─► GET /auth/me (who is this?)
```

**During a run nothing waits for the network.** Each card is committed to `run.db` right
after it drops (RNF19 / RNF20). When the run ends (stack empty, Stop, 250 cards, or a
failure), the Pi's `SyncWorker` uploads the run and all its cards in one request; the
server stores them in **one transaction**. Every run and card carries the UUID it got on
the Pi (`local_uuid`), so a retried upload never creates duplicates. The run is marked
`synced` in `run.db` only after the server confirmed it. If the server is down, the upload
is retried with backoff (10 s, 20 s, 40 s … up to 15 min), also after a reboot.

## Database

PostgreSQL 14+. The schema is in `cardforge_server/migrations/`, applied in order when the
server starts (or with `python -m cardforge_server migrate`) and recorded in
`schema_migrations`:

| File | What |
|---|---|
| `001_init.sql` | DB team's schema v1: `users`, `runs`, `cards` (unchanged) |
| `002_sync_keys.sql` | DB team's `local_uuid` columns + unique indexes for idempotent upload (unchanged) |
| `003_card_details.sql` | What the app shows: `runs.error_code/message/synced_at`; per card: tray (`bin`), number in the run, name, set, collector number, color identity, type, mana cost, rarity, `flag` (`unidentified` / `face_down`). An unidentified card has no `card_identifier` (it was `NOT NULL`). |
| `004_notifications.sql` | RF38: `notifications` — one row per alert of a run, for the user who started it: `kind` (`fault` / `run_ended`), `code`, `status`, `message`, `cards_processed`, `event_time`; `event_id` (unique, chosen by the Pi) makes a resend idempotent. Rows older than 30 days are deleted when a new alert arrives. |

Mapping from the Pi's `local_cards.scan_status`:

| On the Pi | `cards.scan_status` | `cards.flag` | Requirement |
|---|---|---|---|
| `identified` | `success` | NULL | RF19 |
| `unidentified` | `failed` | `unidentified` | RF25 |
| `face_down` | `failed` | `face_down` | RF39 |

The owner of every card (`cards.owner_id`) is the user who pressed Start (`runs.started_by`).

## Running it

```bash
# 1. PostgreSQL: a database and a role (once)
sudo -u postgres psql -c "CREATE ROLE cardforge LOGIN PASSWORD 'cardforge';"
sudo -u postgres createdb -O cardforge cardforge
#    (or Docker: docker compose -f deploy/docker-compose.postgres.yml up -d)

# 2. Dependencies
pip install -r cardforge_server/requirements.txt

# 3. Secrets (keep them out of git)
export CF_DATABASE_URL=postgresql://cardforge:cardforge@localhost:5432/cardforge
export CF_TOKEN_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export CF_MACHINE_TOKENS=$(python -m cardforge_server new-machine-token)
echo "machine token: $CF_MACHINE_TOKENS"     # goes to MTG_MACHINE_TOKEN on the Pi

# 4. Run (applies the migrations first). Interactive docs: http://<host>:8080/docs
python -m cardforge_server
```

On the Raspberry Pi (`deploy/sorter-api.service`, or the shell):

```bash
MTG_SERVER_URL=http://<server-ip>:8080 MTG_MACHINE_TOKEN=<the machine token> python -m sorter_api
```

In the app: `API_BASE_URL=http://<server-ip>:8080` (the server, not the Pi), `USE_MOCK=false`.

| Variable | Default | What it does |
|---|---|---|
| `CF_DATABASE_URL` | `postgresql://cardforge:cardforge@localhost:5432/cardforge` | PostgreSQL connection |
| `CF_TOKEN_SECRET` | random per start | Signs the users' access tokens (HS256). **Set it**, or everyone signs in again after a restart |
| `CF_TOKEN_TTL_HOURS` | `168` | How long a sign-in lasts (7 days) |
| `CF_MACHINE_TOKENS` | *(empty)* | Comma-separated tokens the machines upload with. Empty = no machine can upload |
| `CF_HOST` / `CF_PORT` | `0.0.0.0` / `8080` | Where the API listens |
| `CF_AUTO_MIGRATE` | `true` | Apply `migrations/` on start |
| `MTG_SERVER_URL` (Pi) | *(empty)* | The server. Empty = the Pi works alone as before (runs stay in `run.db`, no owner, no upload; `app_compat.py` answers the app's login) |
| `MTG_MACHINE_TOKEN` (Pi) | *(empty)* | One of the server's `CF_MACHINE_TOKENS` |
| `MTG_SERVER_TIMEOUT_S` (Pi) | `5` | Per request |
| `MTG_SYNC_INTERVAL_S` (Pi) | `60` | How often the Pi looks for runs still to upload (it also uploads right when a run ends) |
| `MTG_SYNC_RETRY_MAX_S` (Pi) | `900` | Longest wait between two failed uploads |

## API

All bodies are JSON. Errors are `{"code": "...", "message": "..."}` (+ `field` for sign-up).
App routes need `Authorization: Bearer <access_token>`; a missing, expired or unknown token,
or a disabled account, answers **401** `UNAUTHORIZED`.

### POST /auth/signup

```json
{ "username": "luca", "email": "luca@example.com", "password": "correct-horse" }
```
**201** — the user is signed in:
```json
{ "access_token": "eyJ…", "token_type": "bearer", "expires_in": 604800,
  "user": { "id": 1, "username": "luca", "email": "luca@example.com",
            "created_at": "2026-11-11T23:34:22.284Z", "last_login_at": null } }
```
| Status | `code` | When |
|---|---|---|
| 422 | `INVALID_USERNAME` | not 3–32 letters, digits, `.`, `_`, `-` (no `@`, so login can tell it from an email) |
| 422 | `INVALID_EMAIL` | not an email |
| 422 | `WEAK_PASSWORD` | fewer than 8 or more than 128 characters |
| 409 | `USERNAME_TAKEN` / `EMAIL_TAKEN` | already used (case-insensitive) |

Passwords are stored as Argon2id hashes (`users.password_hash`), never in clear.

### POST /auth/login

`{ "identifier": "luca" | "luca@example.com", "password": "…" }` → **200**, same body as
sign-up. **401** `INVALID_CREDENTIALS` (same answer and timing for an unknown user and a
wrong password), **403** `ACCOUNT_DISABLED`. `POST /auth/google` answers **501** for now.

### GET /auth/me

The token's user: `{ "id", "username", "email", "created_at", "last_login_at" }`. The Pi
calls it at `POST /run/start` with the app's token to learn who owns the run.

### POST /sync/runs (machine)

`Authorization: Bearer <machine token>`. Sent by the Pi's SyncWorker, never by the app.

```json
{ "run":   { "local_uuid": "9aa0a9c4-…", "started_by": 1, "status": "completed",
             "started_at": "2026-11-11T20:00:00.000Z", "stopped_at": "2026-11-11T20:10:00.000Z",
             "cards_processed": 2, "successful_scans": 1, "failed_scans": 1,
             "error_code": null, "message": "Stack is empty: 2 card(s) processed." },
  "cards": [ { "local_uuid": "…", "owner_id": 1, "card_number": 1, "scan_status": "identified",
               "bin": 1, "card_identifier": "<scryfall id>", "card_name": "Plains", "set_code": "fin",
               "collector_number": "1", "color_identity": "W", "type_line": "Basic Land — Plains",
               "mana_cost": "", "rarity": "common", "match_method": "set_number", "confidence": 100,
               "attempts": 1, "scanned_at": "2026-11-11T20:00:05.000Z", "error_message": null },
             { "local_uuid": "…", "owner_id": 1, "card_number": 2, "scan_status": "face_down",
               "bin": 6, "attempts": 1, "scanned_at": "2026-11-11T20:00:09.000Z" } ] }
```
**200** `{ "run_id": 12, "created": true, "cards_received": 2, "cards_inserted": 2 }`. Sending
the same body again answers `created: false, cards_inserted: 0` and changes nothing else.

| Status | `code` | When |
|---|---|---|
| 401 | `BAD_MACHINE_TOKEN` | token not in `CF_MACHINE_TOKENS` |
| 422 | `INCONSISTENT_RUN` | counters ≠ cards sent, duplicate card numbers, a card owned by someone else, an identified card without id (RNF20) |
| 422 | `UNKNOWN_USER` | `started_by` does not exist |
| 422 | `INVALID_REQUEST` / `INVALID_RUN` | bad body, `status` still running/paused, a database constraint refused it |
| 409 | `RUN_OWNER_MISMATCH` | the run was already uploaded for another user |

Nothing is stored when the answer is not 200.

### GET /runs?limit=50&offset=0

The user's runs, newest first: `{ "runs": [ { "run_id": 12, "local_uuid", "status",
"started_at", "stopped_at", "cards_processed", "successful_scans", "failed_scans",
"error_code", "message", "synced_at" } ], "total": 31 }`.

### GET /runs/{run_id}

Results of one of the user's runs (RF22); **404** `RUN_NOT_FOUND` for anyone else's.
```json
{ "run": { … },
  "bin_counts":  { "1": 1, "4": 2, "6": 1 },
  "scan_counts": { "identified": 3, "unidentified": 0, "face_down": 1 },
  "cards": [ { "card_number": 1, "status": "identified", "bin": 1, "card_name": "Plains",
               "set_code": "fin", "collector_number": "1", "color_identity": "W", "rarity": "common",
               "card_identifier": "…", "type_line": "…", "mana_cost": "", "match_method": "…",
               "confidence": 100, "attempts": 1, "scanned_at": "…" } ] }
```

### GET /cards?query=&limit=30&offset=0

The user's collection (identified cards of all runs) grouped by name, with the copies per
folder — the app's "Cards in folders" contract:
`{ "items": [ { "id": "<scryfall id>", "name": "Lightning Bolt", "quantity": 3,
"folders": [ {"folder": 4, "quantity": 2}, {"folder": 6, "quantity": 1} ] } ], "total": 2 }`.
`query` matches any part of the name, ignoring case.

### GET /cards/export

Every card the user owns, one row per (print, folder, status) with its quantity; identified
cards first, then unidentified and face-down ones. The **app** turns the rows into the CSV
file (RF24, RFC 4180), as in the design (DB05).
```json
{ "rows": [ { "quantity": 2, "name": "Plains", "set_code": "fin", "collector_number": "1",
              "rarity": "common", "color_identity": "W", "type_line": "Basic Land — Plains",
              "mana_cost": "", "folder": 1, "status": "identified", "scryfall_id": "…" },
            { "quantity": 1, "name": null, …, "folder": 6, "status": "face_down", "scryfall_id": null } ],
  "total_cards": 3 }
```

### POST /machine/events (machine) — RF38

An alert of a run, sent by the Pi when it happens. `201 {"id": 12, "created": true}`;
the same `event_id` again → `{"id": 12, "created": false}` (nothing stored twice).
```json
{ "event_id": "c3a5…", "owner_id": 7, "run_id": "<local_uuid>", "kind": "fault",
  "code": "FEED_TIMEOUT", "message": "Feed jam: …", "cards_processed": 4,
  "time": "2026-11-11T20:03:07.000Z" }
```
`kind`: `fault` (needs `code`) or `run_ended` (needs `status`: `completed` / `cancelled` / `failed`).
**422** `UNKNOWN_USER`, `INVALID_EVENT`; **401** `BAD_MACHINE_TOKEN`.

### GET /notifications?run_id=&after=&wait= — RF38

The signed-in user's alerts with an id above `after`, oldest first, and the id to ask after
next: `{"notifications": [ {"id", "run_id", "kind", "code", "status", "message",
"cards_processed", "time", "received_at"} ], "last_id": 12}`. `run_id` limits it to one run
(the app's background service watches the active run). With `wait` (seconds, max 30) and
nothing new yet, the request **waits** for the next alert and returns as soon as it is
stored (long polling; no database connection is held while waiting). Without `after`: no
alerts, just the current `last_id`.

The app uses it from an Android foreground service while a run is active, so the alerts
arrive as notifications even when the app is in the background or the phone is locked —
no Firebase or other push service: only this server.

## On the Raspberry Pi

* `POST /run/start` with a server configured needs `Authorization: Bearer <the app user's
  access token>` (the app sends it). The Pi asks the server `GET /auth/me`; the answer
  becomes `local_run.started_by` and the `owner_id` of every card. Refusals keep the usual
  shape (`started: false`, `problems`):
  **401** `SIGN_IN_REQUIRED` (no/expired token), **503** `SERVER_UNREACHABLE`,
  **502** `SERVER_ERROR`. Only the start needs the server; the run itself does not.
* `GET /sync/status` → `{ "enabled", "server_url", "synced", "pending", "failed",
  "local_only", "last_error" }`. `POST /sync/now` retries the uploads right away.
* RF38: the `AlertForwarder` sends every fault that pauses a run and every run end to
  `POST /machine/events`, for runs that have an owner. It resends with backoff (2, 5, 10,
  30, 60 s) while the server is unreachable, for at most `MTG_ALERT_MAX_AGE_S` (900 s);
  a refusal (4xx) is not resent. `MTG_SERVER_ALERTS=0` turns it off.
* `run.db` (schema version 2) gained `local_run.started_by, owner_name, remote_run_id,
  last_sync_error, synced_at, next_sync_at` and `local_cards.owner_id`. An existing
  `run.db` is migrated in place on start; its old runs have no owner and stay on the Pi
  (`local_only`).

## Security notes

* Use HTTPS in front of the server (a reverse proxy such as Caddy or nginx) whenever it is
  not on the same trusted network: tokens and passwords travel in the requests.
* `CF_TOKEN_SECRET` and the machine tokens are secrets: environment variables or a
  systemd `EnvironmentFile` with mode 600, never in git.
* The Pi's own REST API (port 8000) still has no authentication for machine control, and
  its `/runs`, `/runs/{id}` and `/inventory/export.csv` show every run made on that machine
  (maintenance views). The user's own data comes from the server.

## Tests

```bash
pip install -r requirements.txt -r cardforge_server/requirements.txt
CF_TEST_DATABASE_URL=postgresql://cardforge:cardforge@localhost:5432/postgres python -m pytest -q
```
The role in `CF_TEST_DATABASE_URL` needs `CREATEDB`: each test session makes and drops its
own database. Without the variable the 36 PostgreSQL tests are skipped.
`tests/test_server.py` covers accounts, upload (idempotency, consistency, ownership),
results, collection and export; `tests/test_sync.py` covers the `run.db` migration, the
SyncWorker (backoff, outage), the owner check at Start, and one run end to end
(simulated machine → `run.db` → upload → PostgreSQL → the app's queries);
`tests/test_notifications.py` and `tests/test_alerts.py` cover the alerts (RF38): storage,
one user only, idempotent resend, waiting requests, the Pi's forwarder and one jam end to end.
