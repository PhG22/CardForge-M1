# REST API (for the app)

Base URL: `http://<raspberry-ip>:8000`. Interactive docs: `http://<raspberry-ip>:8000/docs`.
All bodies are JSON. Every response the app should show to the user has a `message` field.

## POST /run/start

This call prepares the machine and checks the start conditions:

1. **Quick checks** (< 1 s): card database loaded, camera crop calibrated, stack IR, platform IR, the 6 tray IRs.
   If any fails, the call answers **409** right away, without homing.
2. **A2** homes the base to the zero position, while **A1** stops the feed roller,
   closes the flap (80°) and sets the chamber light to its base level.
3. The checks are repeated after homing (someone may have touched the machine).

If the checks pass, the sorting loop starts in the background.
⚠️ When the checks pass, the call **blocks while the base homes**: about 5–20 s,
at most 40 s. The app's own routes (`/machines/{id}/start`, end of this page) answer
within 8 s instead.

**200 — run started**
```json
{ "started": true, "message": "Run started.",
  "run": { "run_id": "9aa0a9c4-…", "status": "running", "started_at": "2026-11-04T13:14:28.956Z",
           "stopped_at": null, "cards_processed": 0, "current_tray": null,
           "error_code": null, "message": "Run started." } }
```

**409 — conditions not met.** All problems are listed at once, so the user can fix everything in one go:
```json
{ "started": false,
  "message": "No cards on the stack. Load cards and try again. Trays are not empty (tray 2, 5). Empty them and try again.",
  "problems": [
    { "code": "STACK_EMPTY", "message": "No cards on the stack. Load cards and try again." },
    { "code": "TRAYS_NOT_EMPTY", "message": "Trays are not empty (tray 2, 5). Empty them and try again.", "trays": [2, 5] }
  ] }
```

| Status | `problems[].code` | When |
|---|---|---|
| 409 | `STACK_EMPTY` | Stack IR sees no card |
| 409 | `TRAYS_NOT_EMPTY` | One or more tray IRs see cards (`trays` lists them) |
| 409 | `PLATFORM_NOT_CLEAR` | A card was left on the reading platform |
| 409 | `CARD_DB_MISSING` | `data/cards.db` is missing: run `python -m cardforge.cv sync` |
| 409 | `CROP_NOT_CALIBRATED` | `vision.toml` `[crop] mode = "fixed"` without corners: save the fixed crop on `/calibration` |
| 409 | `RUN_ACTIVE` | A run is already starting / running / stopping / paused |
| 409 | `START_CANCELLED` | `/run/stop` was called during the start checks |
| 503 | `MODULE_OFFLINE` | A1 or A2 did not answer (`module` says which) |
| 500 | `HOME_TIMEOUT`, `PROTOCOL_ERROR`, … | Hardware fault during preparation |

## POST /run/stop

Ends the run with status `cancelled` and leaves no card in the machine:

1. No new card is fed.
2. If a card is in the feeder, the feed finishes and the card reaches the platform.
3. If a card is on the platform, the identification retries are cut short. If the
   card was already identified, the base turns to its tray; otherwise to the
   overflow tray 6. The card is dropped and recorded in run.db like any other.
4. The flap is closed (80°), the roller is stopped, and STOP is sent to every module.

A stop while the run is **paused** just closes the run (the motors are already stopped).

The call answers once all this is done, usually in 1–3 s:
```json
{ "stopped": true,
  "message": "Run stopped by user: the card in progress was sorted, 2 card(s) processed. Flap closed, motors stopped.",
  "state": "idle", "run": { "status": "cancelled", "cards_processed": 2, … } }
```
`POST /run/stop?immediate=true` is the emergency stop. It sends STOP to the modules
at once, and a card may stay on the platform. Use it when something is jammed.

**409** `{ "stopped": false, "message": "No run in progress." }`

## Faults: the run pauses, POST /run/resume continues it

A mechanical fault does **not** end the run (RNF23). The motors stop, the status LED
turns red, the app gets a `fault` event, and the run waits with its id and counters:

| `error_code` | Cause |
|---|---|
| `FEED_TIMEOUT` / `FEED_FAILED` | Feed jam: no card reached the platform |
| `DROP_FAILED` | The card did not fall from the platform |
| `MOVE_TIMEOUT`, `HOME_TIMEOUT`, `HOME_FAILED`, `NOT_HOMED` | The base did not reach a tray / the zero position |

The user fixes the machine (removes the stuck card, clears the feeder) and calls
`POST /run/resume`. It re-homes the base, checks that the platform is clear, and
continues the **same run**. A card that was on the platform when the fault happened
and was removed by hand is not recorded (it was not sorted).

**200** `{ "resumed": true, "message": "Run resumed.", "run": {…} }` ·
**409** `{ "resumed": false, "message": "No paused run to resume." }` ·
**409** with `problems` (e.g. `PLATFORM_NOT_CLEAR`) — the run stays paused.

A module that stops answering (`MODULE_OFFLINE`, Data-Link Loss) or an invalid reply
(`PROTOCOL_ERROR`) still ends the run as `failed`: the machine needs a power cycle.

## Results (RF22), history and CSV export (RF24)

Every card that leaves the platform is written to `run.db` (SQLite on the Pi) in one
transaction, right after the drop. Results are only given once the run is over (RF23).

| Call | Answer |
|---|---|
| `GET /runs?limit=50&offset=0` | `{ "runs": [run, …] }`, newest first |
| `GET /runs/{run_id}` | **200** `{ "run", "bin_counts", "scan_counts", "cards" }` · **409** while the run is running or paused · **404** unknown id |
| `GET /inventory/export.csv` | Every card of every finished run, CSV (RFC 4180, UTF-8, CRLF) |

```json
{ "run": { "run_id": "…", "status": "completed", "cards_processed": 5, "successful_scans": 3, "failed_scans": 2, … },
  "bin_counts": { "1": 1, "5": 1, "6": 3 },
  "scan_counts": { "identified": 3, "unidentified": 1, "face_down": 1 },
  "cards": [ { "card_number": 1, "bin": 1, "scan_status": "identified", "card_name": "Plains",
               "set_code": "FIN", "collector_number": "295", "color_identity": "W",
               "card_identifier": "<scryfall print id>", "match_method": "SET_NUMBER",
               "confidence": 100, "attempts": 1, "scanned_at": "…" }, … ] }
```

Tray rule (RF20): color identity {W} → 1, {U} → 2, {B} → 3, {R} → 4, {G} → 5;
colorless, multicolor, unidentified (after 3 captures, RF25/RF29) and face-down (RF39) → 6.
A run ends by itself after 250 cards (`error_code: "BATCH_LIMIT"`, RNF27).

## Base test endpoints (bottom module A2)

These let you test base moves outside a run. They answer **409** `RUN_ACTIVE`
while a run is going, and **409** `BOTTOM_DISABLED` in top-only mode. Each call
waits until the move is done, then returns the base status:

```json
{ "ok": true, "message": "Base at tray 2.",
  "base": { "state": "IDLE", "homed": true, "position": 2, "steps": 1653, "drift": 0,
            "zero_sensor": false, "trays_with_cards": [], "error": null } }
```

| Call | What it does |
|---|---|
| `GET /base/status` | Live status: homed, tray, steps, sensors |
| `POST /base/home` | Find the strip. The base ends at tray 3, `steps: 0`. Takes up to ~20 s |
| `POST /base/goto/{tray}` | Turn to tray 1–6. Needs homing first (else **409** `NOT_HOMED`) |
| `POST /base/move` | Turn to any absolute position: `{"steps": 1000}` or `{"degrees": 30}` (+ = clockwise). **400** if neither or both are given, or if outside ±20000 steps |

`POST /run/stop` also stops a base test move.

`drift` is the number of steps the base was off when it last re-homed. Use it to
check for lost steps: home → do some moves → home again → read `drift`. 0 to ±5 is
fine. Avoid calling `GET /base/status` in a fast loop while the base is moving,
because every request on the bus makes the motor stagger slightly.

## GET /stack

Reads the stack IR on the top module (A1):
```json
{ "has_cards": false, "message": "No cards on the stack." }
```
Answers **503** with `has_cards: null` if A1 does not respond.

## GET /run/status

Returns the current controller state and the current or last run. Use it to poll,
or to catch up after the WebSocket was disconnected.
```json
{ "state": "running", "run": { "run_id": "…", "status": "running", "cards_processed": 7, "current_tray": 1, … } }
```
`state`: `idle` · `starting` · `running` · `stopping` · `paused`
`run.status`: `running` · `paused` · `completed` · `failed` · `cancelled`.
When it is `paused` or `failed`, `run.error_code` and `run.message` say why.
`run.current_tray` is `null` while the run is active (RF23).

## WS /ws/events — notifications

Connect once and keep the socket open. Each message is one JSON object:

| `type` | When | Payload |
|---|---|---|
| `snapshot` | Right after connecting | same as GET /run/status |
| `run_started` | Start checks passed | `run` |
| `card_sorted` | A card was dropped in a tray | `run_id`, `card_number` — progress only: no tray, no card identity (RF23) |
| `card_cleared` | A stop sorted the card in progress | `run_id`, `card_number` |
| `attention` | A card went to tray 6 unidentified or face-down; the run continues (RF36) | `kind` (`unidentified` / `face_down`), `run_id`, `card_number`, `time` |
| `fault` | **The run paused. Alert the user** (RF27) | `fault` (error code), `time`, `message`, `run` |
| `run_resumed` | `/run/resume` succeeded | `run` |
| `run_finished` | **Run ended. Notify the user** | `run` (with `status` completed / failed / cancelled), `message` |

Example `run_finished` messages: `"Stack is empty: 37 card(s) processed."`,
`"The feeder could not move a card onto the platform (jam or empty stack)."`,
`"The bottom module (A2) stopped responding."`.

## GET /hardware/status

Returns the live STATUS of both modules, for a debug or maintenance screen:
```json
{ "top":    { "online": true, "state": "IDLE", "stack_has_card": true, "platform_has_card": false,
              "flap_angle": 80, "roller_running": false, "error": null },
  "bottom": { "online": true, "state": "IDLE", "homed": true, "position": 1, "zero_sensor": true,
              "trays_with_cards": [], "error": null } }
```

## GET /health

Returns `{ "ok": true, "simulate": false, "bottom_enabled": true }`.

## Top-only mode (`--top-only` / `MTG_BOTTOM_ENABLED=0`)

This mode is for the test bench where only the top module (A1) is wired. The
API never talks to A2:

* no homing
* no tray check
* no base moves

cards are recorded with `bin: null`, and `GET /hardware/status` reports
`bottom.enabled: false`. Everything else works the same way: the start checks
(stack, platform), the loop (feed → identify → drop), stop, and the
notifications.

## Routes of the mobile app (app_compat.py)

The app (CardForge App, `lib/core/api/api_contract.dart`) talks to `API_BASE_URL`. Until
the CardForge server exists, the Pi serves those routes itself: build the app with
`USE_MOCK=false` and `API_BASE_URL=http://<raspberry-ip>:8000`.

| Route | Answer |
|---|---|
| `POST /auth/login` `{identifier, password}` | **200** `{access_token}` · **401**. User `MTG_APP_USERNAME` (default `admin`), password `MTG_APP_PASSWORD` (empty = anything is accepted) |
| `POST /auth/google` | **501**: Google sign-in needs the CardForge server |
| `GET /cards?query=&limit=&offset=` | `{items: [{id, name, folders: [{folder, quantity}]}], total}` — identified cards of finished runs, by name |
| `GET /machines/{id}/status` | `{state}`: `idle`, `running` (starting / running / stopping), `stopped` (paused by a fault) |
| `POST /machines/{id}/start` | Starts a run, or **resumes** a paused one. Waits up to 8 s: a refusal comes back as **409** with `problems`; after 8 s (still homing) the answer is `running` |
| `POST /machines/{id}/stop` | Normal stop, `{state}` (waits up to 8 s) |

They need `Authorization: Bearer <access_token>` (else **401**). The `{id}` is ignored:
one Pi drives one machine. The app shows any **409** as "machine offline", so the
reason of a refused start is only in the body for now.

## Camera calibration (calibration_api.py)

With the machine assembled, from a browser: `http://<raspberry-ip>:8000/calibration`. The page uses these
routes; all answer **409** `RUN_ACTIVE` during a run and **409** `VISION_OFF` with `MTG_VISION=off`.

| Route | What it does |
|---|---|
| `POST /calibration/snapshot` | One capture: `metrics` (frame brightness, clipped %, card found, area, 3×3 light grid, sharpness, advice) + annotated JPEG (base64) |
| `GET /calibration/frame.jpg?overlay=1` | One capture as JPEG |
| `GET / PUT /calibration/settings` | `exposure_us`, `analogue_gain`, `awb_gains` [red, blue], `light_level` — PUT applies them live, not saved |
| `POST /calibration/auto` | Auto exposure + white balance with the card in place, then locked |
| `POST /calibration/save` | Writes the values to `vision.toml` `[camera]` |
| `POST /calibration/reference` | Remembers the card brightness; later snapshots report the change (RNF08, ≤ 10 %) |
| `POST /calibration/identify` | Full identification of the card on the platform + annotated card |
| `POST /calibration/feed` / `POST /calibration/drop` | Feed one card onto the platform / drop it into tray 6 (homes the base if needed) |
| `GET /calibration/crop` | Fixed crop: `mode`, saved `corners`, measured `samples`, their `mean` and `spread_px` |
| `POST /calibration/crop/sample` | Find the card on the platform by border detection (calibration only) and keep its corners. The picture is saved in `calibragem_recorte/` (original, `_anotada`, `medicoes.csv`; `_sem_borda` when not found) and its name comes back in `picture` |
| `DELETE /calibration/crop/samples` | Forget the measurements |
| `POST /calibration/crop/save` | The average position becomes the fixed crop, live and in `vision.toml` `[crop]` |

With the fixed crop the identification never searches for the card border: it warps the picture with the
saved corners (`strategy: "FIXED"` in the result). `MTG_VISION=files` (photos from elsewhere) always uses
border detection until a crop is saved.

## Simulator only (`--simulate`)

* `GET /sim/state` shows the simulated machine.
* `PUT /sim/state` sets it up for testing the app's error screens:
```json
{ "stack_cards": 10, "trays_with_cards": [], "platform_has_card": false,
  "top_jammed": false, "offline_modules": [] }
```
`GET /sim/state` also shows `light_level` (the optional LIGHT command, PROTOCOL.md 5.1) and
`status_led` (the mode the Pi's RGB LED is showing). `MTG_SIM_OPTIONAL_COMMANDS=0` simulates
`a1_top.ino` as it is today, without LIGHT.
