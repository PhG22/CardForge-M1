# Changelog

All notable changes to CardForge-M1: the Raspberry Pi main loop and control API (`sorter_api/`), the computer
vision and Scryfall mirror (`cardforge/`), the Arduino firmware (`firmware/`), the CardForge server
(`cardforge_server/`), the Bluetooth Wi-Fi setup (`pi_ble_setup/`) and the mobile app (`cardforgeapp/`).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has no version
numbers yet, so entries are grouped by the date the changes reached `Integration_branch`; the short hashes point
to the commits.

## 2026-11-18

### Changed

- **The card database is no longer updated at power-on.** An update can take minutes on a slow connection and
  held the machine until it ended. Now the service only **checks** Scryfall when it starts (2 small requests,
  in the background): the machine is ready and sorts as usual, and the status LED no longer blinks during the
  check. The update happens only when the user starts it from the app.
- Settings renamed for this: `MTG_CARD_DB_UPDATE` → `MTG_CARD_DB_CHECK` (check at power-on: `auto`, `on`,
  `off`) and `MTG_CARD_DB_UPDATE_WAIT_S` → `MTG_CARD_DB_CHECK_WAIT_S`.

### Added

- **Card database API** (`sorter_api/card_db_update.py`, docs/API.md "Card database"):
  - `GET /card-db`: is an update available; is a check or an update running, with its progress.
  - `POST /card-db/check`: check Scryfall now.
  - `POST /card-db/update` (`?force=true`): start the update in the background.
  - `POST /card-db/update/cancel`: stop it; the local database is kept.

  The same status is `card_db` in `GET /run/status` and in the WebSocket's first snapshot, and a `card_db` event
  goes out when a check or an update starts or ends, and about once a second while an update runs.

  While an update runs:
  - the status LED blinks white;
  - Start is refused (`409 CARD_DB_UPDATING`);
  - the update itself is refused while a run is active (`409 RUN_ACTIVE`).
- **Update progress and ETA.** The sync reports each block downloaded and each 1,000 cards built, and can be
  cancelled. `progress` gives:
  - the stage (Portuguese names, card list, building, finishing);
  - the overall fraction (it never goes backwards);
  - MB downloaded out of the total, and the speed;
  - the time left, from the speed so far and how long the last build took on this machine.

  `cards.db` now records `build_seconds`, `build_machine` and `default_cards_lines` for this.
- **App** (`cardforgeapp/`):
  - A **Card database** panel on Machine control:
    - shows whether the cards are up to date or an update is available (with its size), and the local database's
      date and number of printings;
    - while updating: a progress bar with the stage, %, MB and time left, and **Cancel update**;
    - **Check for updates**, **Update now** (with a confirmation that states the size and that the machine can't
      sort meanwhile) and **Update anyway**.
  - Alerts: "Card database update available" (once per version of Scryfall's data), "Card database updated" and
    "Card database update failed".
  - A line on the Home machine card while an update is available or running.
  - Start is disabled during an update, and the `CARD_DB_UPDATING` refusal has its own text, in English and
    Portuguese.
  - The in-app demo machine (`USE_MOCK_MACHINE`) simulates the whole flow.
- Tests:
  - Python `test_card_db_update.py`: 26 tests (check at power-on, update on request, progress / ETA, cancel,
    the routes); 243 passed, 4 skipped.
  - App `card_db_test.dart`: 12 tests (JSON, controller, screens in English and Portuguese).

## 2026-11-17

### Added

- **Portuguese card names in the card database.** The sync (`python -m cardforge.cv sync`) also reads Scryfall's
  `all_cards` bulk (~400 MB) as a stream: nothing of it is written to the SD card, and only the lines with
  `"lang":"pt"` are parsed. Each Portuguese print's `printed_name` goes to the new `card_print_name` table, and
  the translated names join the name index (`card_name.lang = 'pt'`; a translation never takes the place of an
  English name of another card). With that:
  - a title read in Portuguese confirms the print found by set + number (`SET_NUMBER`, 100 %,
    `debug.printed_name_match`), and finds the card alone when the number was not read (`NAME_EXACT` /
    `NAME_FUZZY`);
  - Portuguese titles no longer trigger the 180° title re-read or the layout OCR.

  Languages: `printed_name_langs` in `vision.toml` (default `["pt"]`) or `sync --langs` (`--langs ""` = English
  only). Measured on the 10 machine photos in `calibragem_recorte/` (7 of them in Portuguese):

  | | identified | OCR calls | time per photo (PC, median) |
  |---|---|---|---|
  | before | 3/10 | 24 | 603 ms |
  | after | 9/10 | 11 | 190 ms |

  Cost: the name index grows from 40 k to 66 k names (+4.6 MB of RAM).
- **Card database update at power-on** (`sorter_api/card_db_update.py`). Every time the service starts it asks
  Scryfall for new cards (2 small requests). If there are any, `data/cards.db` is updated before the machine is
  ready: the base homes and the status LED leaves "starting" only after the update. Up to date, no internet or
  Scryfall unreachable: the machine starts with the local database. Details:
  - The download and the new database are built while the old one stays in use; the files are swapped at the
    end, so a failed update never leaves a half-written database.
  - At boot, with Wi-Fi configured but still connecting, it waits up to 30 s (`MTG_CARD_DB_UPDATE_WAIT_S`);
    with no Wi-Fi configured it does not wait.
  - While it runs, `POST /run/start` answers `409 CARD_DB_UPDATING`; the API keeps answering.
  - `MTG_CARD_DB_UPDATE`: `auto` (the real machine with the camera, not `--simulate`), `on`, `off`.
- **Status LED `UPDATING`**: blinking white (1 Hz) while the card database updates at power-on, above every other
  state. An addition to Appendix B, like "Starting" (solid white).
- **Run benchmark** (`sorter_api/benchmark.py`). At the end of every run: mean, median, mode (times rounded to
  0.1 s), minimum and maximum of each card's identification time (`identify_s`: first photo → decision, retries
  included) and whole cycle (`total_s`: start of the feed → end of the drop), for every card (`general`) and for
  the cards read at the first try with no failsafe (`first_try`). Each card that is not first-try says why
  (`fallbacks`: `retry`, `orientation_reread`, `layout_ocr`, `face_down_after_identify`, `not_identified`,
  `vision_error`, `feed_retry`). Saved in:
  - `benchmarks/benchmark_runs.csv` (every run) and one JSON per run;
  - `run.db` (table `run_benchmark`);
  - `GET /runs/{id}` (`benchmark`) and, live, `GET /run/status` (`timing.benchmark`).
- **`cards_to_improve/` dataset** (`sorter_api/slow_cards.py`). Every card whose identification takes longer than
  3 s (`MTG_SLOW_CARD_S`) is kept: for each capture, the camera picture and the annotated crop; plus
  `resultado.json`, with the times of each phase and capture, what each capture read and matched, and the final
  decision. Each run also gets an `indice.csv` and a `resumo.json`. The pictures are written by a lowest-priority
  thread while the base turns, so a slow card does not slow the next one. Capped at `MTG_SLOW_CARDS_MAX_MB`
  (1000 MB); above it, slow cards keep only their JSON.
- `GET /health` and `GET /run/status`: `startup` (`card_db` / `homing` while the power-on sequence runs) and
  `card_db` (result of the check: `up_to_date`, `updated`, `offline`, `unreachable` or `failed`, with the
  download progress while it runs).
- Settings: `MTG_CARD_DB_UPDATE`, `MTG_CARD_DB_UPDATE_WAIT_S`, `MTG_SLOW_CARD_S`, `MTG_SLOW_CARDS_DIR`,
  `MTG_SLOW_CARDS_MAX_MB`, `MTG_BENCHMARK_DIR`. In `vision.toml`: `printed_name_langs`, `db_max_age_days`.
- Tests: `test_printed_names.py`, `test_run_metrics.py`, `test_card_db_update.py` (47 new; 227 passed,
  4 skipped).

### Changed

- **"Up to date" for the card database means the same cards, not the same file.** Scryfall rebuilds its bulk
  files every day (prices, images, legalities), which changes nothing for the identification. `sync` and the
  power-on check now rebuild only when one of these changes:
  - the fingerprint of the physical sets and their card counts (`sets_fingerprint`: a new set, new cards, The
    List / Secret Lair drops);
  - the database schema;
  - the printed-name languages;
  - the age of the database, older than `db_max_age_days` (30). This catches Portuguese names that Scryfall adds
    after a release.

  `sync --force` still rebuilds no matter what.
- `data/cards.db` rebuilt from Scryfall's 2026-11-17 data: schema 2, 110,960 prints, 66,442 names (26,041 of
  them in Portuguese, from 38,636 Portuguese prints), with the sets fingerprint. Size: 48 MB (was 41 MB).
- `run.db` schema 3, migrated in place when the service starts:
  - `local_cards` gets `identify_s`, `total_s`, `first_try` and `fallbacks`;
  - new table `run_benchmark`.

  The upload to the CardForge server sends the same fields as before.
- Power-on sequence: card database check/update → base homing (RF08) → status LED leaves "starting".
- `VisionModule.update_database()` holds the vision lock only to swap the database file, not during the
  download.
- `VisionModule.start()` configures the camera even when `cards.db` is missing, so a database created by the
  power-on update can be used right away.
- The end-of-run cleanup never deletes `cards_to_improve/` or `benchmarks/`; both are in `.gitignore`.
- The sync logs its download progress every 10 %, and the CLI shows it. Its requests give up after 10 s
  without a connection.
- `BulkEntry.size` reads Scryfall's `compressed_size` (the `size` field no longer exists).

### Fixed

- Portuguese cards going through all 3 captures (RF29) and ending in bin 6 because their title matched no
  English name. This was the main cause of the erratic identification time.

### Deploy notes (Raspberry Pi)

- `git pull`, then `sudo systemctl restart sorter-api`. `run.db` migrates by itself. The `data/cards.db` in the
  repository is current, so the first start checks but does not download.
- When Scryfall gets new cards, the next start downloads ~480 MB and rebuilds the database. This takes a few
  minutes on the Pi 3B, with the LED blinking white.
- The mobile app is unchanged. For `CARD_DB_UPDATING` it shows the Pi's own message, as it does for any code it
  does not know.

## 2026-11-16

- Collection by run and by color, CSV/TXT export of the collection, no card list while a run is active
  (server, app). `ea1c4c8`
- Flap angle adjustment (`a1_top.ino`, settings). `71b6fb1`
- Base IR sensors fixed (`a2_base.ino`). `99232f5`

## 2026-11-15

- Portuguese cards identified at the first capture: the info line gives ranked edition / number candidates
  (language tag glued or misread, digits sanitized), checked against `card_print`; the language tag confirms a
  localized card; the card back is told by its color before any OCR (639 → 6 ms). `4739d79`
- A2 stepper bench test: diagnostic firmware `firmware/a2_motor_test/` and the PC tool
  `tools/a2_motor_test.py`. `2d48ed3`
- Face-down check after a failed identification (RF39). `d24ddc5`
- Runs limited to 250 cards (RNF27). `e89ef10`
- Last-run check (RNF19): the app warns when the last run did not finish correctly. `2e9065b`
- Status LED: solid white "Starting" from power-on (`gpio=17,22,27=op,dh` in `config.txt`); the service no
  longer waits for `network-online.target`. `51df3f3`
- Flap angles tuned. `788f880`, `1771dda`, `1ac1719`, `2e549a6`

## 2026-11-12

- Camera auto-detection (IMX219 or OV5647) with a full-resolution profile per sensor; manual crop corners on the
  calibration page. `d07bf24`
- Manual flap control (`GET/POST /flap`, `PUT /flap/angles`), app and deploy changes. `8e7154a`
- Notifications (RF38), feed pull attempts (RF05), fast homing (RF08), RS-485 retransmissions (RNF10), run
  timing. `f631243`
- Machine photos for the fixed-crop calibration (`calibragem_recorte/`). `9e73aeb`
- Homing IR orientation (`a2_base.ino`). `ea9dd90`

## 2026-11-11

- CardForge server (FastAPI + PostgreSQL): sign-up, run upload, run history, CSV export, Resume, fault alerts,
  power-on homing. `79cd94b`
- Status LED control on the Pi GPIO, bin IR sensor check before a run, fixed crop for the vision, temporary
  files cleanup at the end of each run, legacy proof of concept moved to `deprecated/`. `a9ef7bc`
- `.gitignore`: patch files. `5485ea6`

## 2026-11-10

- Bluetooth Wi-Fi setup: network changes and Wi-Fi drop fix. `5184962`, `a0e08c7`

## 2026-11-09

- Mobile app (Flutter) and the Bluetooth Wi-Fi setup service (`pi_ble_setup/`). `31538e3`

## 2026-11-07

- Main loop on the Pi integrating firmware, control API, computer vision and app: vision identification with up
  to 3 captures (RF29), tray by color identity (RF20), bin 6 for unidentified / face-down cards (RF25 / RF39),
  `run.db` with one record per card (RF19, RNF19 / RNF20), pause on mechanical faults + Resume (RNF23), results
  and CSV (RF22 / RF24), the routes the app uses, camera calibration over the network (`/calibration`), firmware,
  deploy files and docs. `305c57a`

## 2026-11-04

- Initial commit: computer vision module (`cardforge/`) and local Scryfall mirror. `db653e2`, `ab7010d`
