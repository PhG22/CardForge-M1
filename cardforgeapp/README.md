# CardForge

Mobile app (Flutter/Dart) to monitor and manage an automatic **Magic: The
Gathering** card sorter.

The physical machine holds a stack of cards, pulls one at a time with a roller,
identifies it with a camera and drops it in the right folder according to the
sorting rule defined in the app. CardForge is the control panel for that machine.

> **Status:** early development. Splash, authentication, machine setup over
> Bluetooth, machine control over Wi-Fi, alerts, cards in folders and settings are
> in place. The remaining screens are listed in the [roadmap](#roadmap). By default
> everything runs against simulated services; see
> [Testing with the machine](#testing-with-the-machine) to use the real Raspberry Pi.

## Screens

| Screen | Route | What it does |
|---|---|---|
| Splash | `/splash` | Checks the server (`GET /health`) and decides where to go: Login (no session) or Home (saved session). If the server does not answer it shows an error with **Try again**. |
| Login | `/login` | Username or email + password, **Remember me** and Google sign-in. |
| Home | `/home` | Shows the linked machine and its connection (tap it for machine control), or a **Link machine** button; the alerts bell with an unread badge and the side menu. |
| Link machine | `/link-machine` | Wizard: Bluetooth scan, pick a machine, machine password, Wi-Fi details, wait for the machine to join the network and check it answers over Wi-Fi. |
| Machine control | `/machine` | Start / stop a sorting run (and emergency stop), live progress, why a run can't start, and diagnostics (module sensors, base test moves). Talks to the Raspberry Pi's REST API over Wi-Fi. |
| Alerts | `/alerts` | Every event reported by the machine, newest first. Tap to mark as read, or mark/clear all. |
| Cards in folders | `/cards` | Search a card by name and see which folders (1 to 6) it is in; with nothing typed, lists the cards in the folders. Data comes from the server. |
| Settings | `/settings` | Language (English or Portuguese) and, in debug builds only, a tool to simulate alerts. |

Side-menu items that do not have a screen yet (CSV report, sorting rules) show a
"coming soon" message.

### Navigation flow

```
Splash -- server ok? -- no --> Splash (error) -- Try again --+
   |                                ^------------------------+
  yes
   |
saved token? -- yes --> Home <---------------------------+
   |                     |  menu: Link machine, Alerts,  |
   no                    |  Settings                     |
   |                     v                               |
 Login -- success -------+                               |
```

## Language

The app is written in English and starts in English. **Settings > Language**
switches the whole app to Portuguese and remembers the choice.

- All text lives in `lib/core/l10n/`: `app_strings.dart` declares every string,
  `strings_en.dart` and `strings_pt.dart` implement them. If a language misses a
  string, the build fails, so nothing shows up blank.
- In a widget: `context.l10n.signIn`.
- **Adding a text:** declare it in `AppStrings`, then implement it in both
  languages.
- **Adding a language:** create a new `AppStrings` subclass, then register it in
  `AppStrings.supportedLocales` and `AppStrings.forLocale`.

## Machine linking (first setup, Bluetooth)

Bluetooth is used **only once**, to set the machine up. After that the app talks
to the machine over Wi-Fi.

1. The app scans over Bluetooth Low Energy for CardForge machines (the Raspberry
   Pi runs the `pi_ble_setup` service) and lists them with their signal strength.
2. The user selects one and enters the **machine password** (set on the Pi with
   `python -m cardforge_setup set-password`). Five wrong passwords lock it for a minute.
3. The app asks for the **Wi-Fi details** (network name and password). The user can
   skip this only if the machine is already on the network.
4. The machine joins the network and reports its IP address. The app then
   checks that **the phone reaches the machine's REST API over Wi-Fi**
   (`GET http://<ip>:8000/health`). Each step is shown; it times out after 90 s.
5. On success the machine (name, Bluetooth id, IP and port) is saved on the phone
   and two alerts are raised ("Machine linked" and, if Wi-Fi was sent, "Machine
   connected to Wi-Fi"). On failure the user sees why (wrong Wi-Fi password, network
   not found, phone on another network, ...) and can fix the Wi-Fi details or start over.

The protocol (GATT UUIDs, JSON messages, error codes) is in
[`docs/ble-setup-protocol.md`](docs/ble-setup-protocol.md). In the code:

| File | What it does |
|---|---|
| `machine/machine_link_flow.dart` | The wizard's state machine (no widgets) |
| `machine/machine_link_service.dart` | Interface: scan, authenticate, link, find address |
| `machine/ble/ble_machine_link_service.dart` | Real transport (flutter_blue_plus) |
| `machine/ble/ble_setup_codec.dart` | Message framing and request/reply, pure Dart |
| `machine/mock_machine_link_service.dart` | Simulated machine for the emulator |

**Simulated Bluetooth** (`USE_MOCK_BLUETOOTH=true`, the default) works in the emulator:

- two machines are "found": `CardForge-A1B2` and `CardForge-9F3C`;
- machine password `0000` is rejected, any other is accepted;
- a Wi-Fi network named `fail` is "not found" and the Wi-Fi password `wrongpass` is
  rejected (the same rules as the Pi's `--simulate-wifi`);
- the machine reports the address `MOCK_MACHINE_HOST` (default `10.0.2.2`, the
  computer running the Android emulator).

## Machine control (Wi-Fi, REST API)

Once linked, `MachineController` (app-wide, in `AppScope`) connects to the
Raspberry Pi's REST API (`sorter_api`, see `raspberry-api/rest-api.md` in the
project docs) and keeps the `WS /ws/events` socket open:

- **Start** (`POST /run/start`) waits while the base homes (up to ~40 s). If the
  machine can't start, every reason is listed at once (no cards on the stack,
  folders not empty, card left on the platform, module offline...).
- **Stop** (`POST /run/stop`) finishes the card in progress; **Emergency stop**
  (`?immediate=true`) stops the motors at once.
- **Live progress** from the WebSocket: cards processed, current folder, the last
  cards sorted.
- **Alerts on any screen:** run finished, card jam (`FEED_TIMEOUT`), run failed,
  connection lost during a run. A run that ended while the phone was offline is
  announced when it reconnects.
- **Offline:** the app retries on its own (2 s, 5 s, 10 s, 30 s). If the router gave
  the machine a new IP, **Find over Bluetooth** asks the machine for it (no password
  needed) and saves it.
- **Diagnostics:** live module status (`GET /hardware/status`) and base test moves
  (`POST /base/home`, `/base/goto/{n}`), for calibration on the bench.

| File | What it does |
|---|---|
| `machine/api/machine_api.dart` | `MachineApi` interface and `HttpMachineApi` (http + WebSocket) |
| `machine/api/machine_api_models.dart` | JSON models of the REST API and events, pure Dart |
| `machine/api/mock_machine_api.dart` | Simulated machine inside the app |
| `machine/machine_controller.dart` | Connection, run state, commands, alerts |
| `machine/machine_control_screen.dart` | The screen |

## Testing with the machine

Four levels, from no hardware to the full machine. Levels 1 to 3 need only a
computer (macOS or Linux) and an Android phone:

| # | What you test | Phone | Run on the Pi / computer | `flutter run` flags |
|---|---|---|---|---|
| 1 | UI only, everything simulated | Emulator or phone | nothing | none |
| 2 | App ↔ real REST API, no Bluetooth | Emulator or phone | `python -m sorter_api --simulate` on your computer, listening on `0.0.0.0:8000` | `--dart-define=USE_MOCK_MACHINE=false` |
| 3 | Real Bluetooth setup, the computer plays the machine | **Real phone** (emulators have no Bluetooth) | level 2 + `python -m cardforge_setup --config ./setup.json run --simulate-wifi` on the computer | `--dart-define=USE_MOCK_BLUETOOTH=false --dart-define=USE_MOCK_MACHINE=false` |
| 4 | Full setup and control | **Real phone** | `cardforge_setup` + `sorter_api` on the Pi | same as 3 |

- Level 2 on a real phone instead of the emulator: add
  `--dart-define=MOCK_MACHINE_HOST=<your computer's Wi-Fi IP>`.
- Switching levels: **Forget this machine** (menu on Machine control) and link again,
  because each link saves the address it got.
- Levels 3 and 4: phone and machine must be on the same Wi-Fi. Campus and guest networks often
  block devices from talking to each other; a phone hotspot or a home router works.
- In the setup, "control service is not running" means `sorter_api` is not up on the
  Pi; "this phone cannot reach it" means the phone is on another network (or mobile data).
- Debug the Bluetooth side without the phone: the free **nRF Connect** app can
  connect to `CardForge-XXXX` and write `{"id":1,"cmd":"info"}` + newline.
- The Pi side of the setup is the `pi_ble_setup` service. It lives with
  `sorter_api` in the Raspberry Pi code, not in this repo; its README explains the
  install (`run --simulate-wifi` tests Bluetooth without changing the Pi's Wi-Fi).

## Alerts

Alerts are events from the machine, shown in two places: a **banner** over
whatever screen is open (with a **View** button) and the **Alerts** screen
(opened from the bell or the menu).

Available types: Wi-Fi connected, Wi-Fi lost, sorting finished, card jam, double
feed, connection lost and machine linked.

- An alert stores its type and data (machine name, card count), not text, so it
  is translated when shown and follows the current language.
- Alerts live in memory for now. When the server connection exists (push,
  WebSocket or polling), it only has to call `AlertsController.add(...)`.
- **Adding a type:** add it to `AlertType` (and its severity), then the title and
  message in both languages, plus an icon in `alert_style.dart`.
- In debug builds, **Settings > Developer tools** creates each alert on demand.

## Cards in folders

The machine has **six folders, numbered 1 to 6**. The screen has a search bar on
top and the cards below it:

- **Nothing typed:** lists the cards that are in the folders (alphabetical), each
  with the folders it is in and how many copies are there.
- **Searching by name:** lists the matching cards and the folders each one is in.
  A card can be in more than one folder.
- **Everything comes from the server** (`GET /cards`). The search waits 400 ms
  after the last keystroke (or the keyboard's search key), results load in pages
  of 30 as you scroll, and pull-to-refresh reloads the list. An answer to an old
  search never replaces the newer one.
- **Errors:** no connection or a server problem shows **Try again**; an expired
  session shows **Sign in again**.

With the simulated backend (the default) there are 36 sample cards spread over
the six folders. Searching for `fail` simulates a server error.

Expected response of `GET /cards?query=bolt&limit=30&offset=0` (`query` is left
out to list every card; the request carries `Authorization: Bearer <token>`):

```json
{
  "items": [
    {
      "id": "c-123",
      "name": "Lightning Bolt",
      "folders": [
        { "folder": 4, "quantity": 4 },
        { "folder": 6, "quantity": 1 }
      ]
    }
  ],
  "total": 1
}
```

`total` is the number of cards that match, not the size of the page. Folder
numbers outside 1 to 6 are ignored.

## Requirements

- Flutter **3.22 or newer** (Dart 3.4+)
- Android Studio (Android SDK) and/or Xcode, depending on the target platform
- An emulator or phone (`flutter devices` lists what is available)

## Running

```bash
flutter pub get
flutter run
```

By default the app uses a **simulated backend** (`USE_MOCK=true`): the Splash
passes right away and any valid sign-in works (a username or email and a
password of 6+ characters). You can try every screen without a server.

### Pointing at the real server

```bash
flutter run \
  --dart-define=USE_MOCK=false \
  --dart-define=API_BASE_URL=https://your-server.com \
  --dart-define=GOOGLE_SERVER_CLIENT_ID=YOUR_WEB_CLIENT_ID.apps.googleusercontent.com
```

| Variable | Default | Description |
|---|---|---|
| `USE_MOCK` | `true` | Use simulated server responses. |
| `USE_MOCK_BLUETOOTH` | `true` | Use the simulated machine for linking (set `false` on a real phone). |
| `USE_MOCK_MACHINE` | `true` | Use the simulated machine REST API inside the app. |
| `MOCK_MACHINE_HOST` / `MOCK_MACHINE_PORT` | `10.0.2.2` / `8000` | Address the simulated linking reports (the emulator's host computer). |
| `API_BASE_URL` | `https://api.cardforge.example` | API base URL. |
| `GOOGLE_SERVER_CLIENT_ID` | empty | OAuth client ID of type *Web* (Google Cloud). Needed for Google sign-in to return the `idToken` on Android. |

They are read in `lib/core/config/app_config.dart`.

## Backend contract

| Method and route | Body | Response |
|---|---|---|
| `GET /health` | n/a | `200` if the server is up |
| `POST /auth/login` | `{"identifier": "...", "password": "..."}` | `200` with `{"access_token": "..."}`, or `401` for invalid credentials |
| `POST /auth/google` | `{"id_token": "..."}` | `200` with `{"access_token": "..."}`; the backend validates the Google token |
| `GET /cards?query=&limit=&offset=` | n/a | `200` with `{"items": [...], "total": N}` (see [Cards in folders](#cards-in-folders)); `401` if the token is invalid |

The token is kept in the device's secure storage (`flutter_secure_storage`). With
**Remember me** unchecked, the session lasts only until the app is closed.

If your backend uses other routes or formats, the change is limited to
`lib/features/auth/auth_service.dart` and
`lib/core/services/server_health_service.dart`.

## Google sign-in

The code is ready; the configuration lives outside it:

1. In Google Cloud Console create OAuth credentials: a **Web** client (its ID goes
   in `GOOGLE_SERVER_CLIENT_ID`), an **Android** client with the package name and
   the certificate SHA-1, and an **iOS** client if you target iOS.
2. Debug SHA-1: `cd android && ./gradlew signingReport`.
3. On iOS, add the client ID and URL scheme to `Info.plist`.
4. On the backend, validate the `id_token` received at `POST /auth/google` before
   issuing your own token.

The button uses a generic icon. Replace it with the official Google logo (asset)
before publishing.

## Android setup

- **`minSdk`:** `flutter_secure_storage` needs at least 23. In
  `android/app/build.gradle` (or `.kts`) set `minSdk = 23`.
- **Permissions** (already in `AndroidManifest.xml`): `INTERNET`, and for Bluetooth
  `BLUETOOTH_SCAN` (with `neverForLocation`) and `BLUETOOTH_CONNECT` on Android 12+,
  `BLUETOOTH`, `BLUETOOTH_ADMIN` and `ACCESS_FINE_LOCATION` on Android 11 and older.
  flutter_blue_plus asks for them at run time.
- **Plain HTTP:** the machine's API is `http://` on the local network and its IP is
  only known at run time, so `android:usesCleartextTraffic="true"` is set. The
  server API should still use HTTPS.
- **Local server from the emulator:** use `http://10.0.2.2:PORT` instead of `localhost`.

## iOS setup

`Info.plist` has `NSBluetoothAlwaysUsageDescription` (Bluetooth),
`NSLocalNetworkUsageDescription` (iOS asks before talking to devices on the Wi-Fi)
and `NSAllowsLocalNetworking` (plain HTTP to local addresses).

## Project structure

```
lib/
├── main.dart                         Entry point
├── app.dart                          MaterialApp, language, alert banners
├── core/
│   ├── app_scope.dart                Shared objects (language, alerts, machine)
│   ├── config/app_config.dart        Environment variables (dart-define)
│   ├── l10n/                         Strings (EN and PT) and language controller
│   ├── router/app_router.dart        Routes (go_router)
│   ├── services/server_health_service.dart
│   ├── theme/app_theme.dart          Colors (MagicColors) and Material theme
│   └── widgets/                      Design system components
└── features/
    ├── splash/
    ├── auth/                         AuthService + login screen
    ├── home/
    ├── settings/
    ├── alerts/                       Model, controller, banner style, screen
    ├── cards/                        Models, repository (API + mock), controller, screen
    └── machine/                      Linking (flow, screen, store), control (controller, screen)
        ├── ble/                      Real Bluetooth setup transport and its codec
        └── api/                      Machine REST API client, models, simulated machine

assets/images/                        mana_symbols.png (transparent background)
test/                                 Tests (see Testing)
docs/diagrams.md                      Diagrams (Mermaid)
docs/ble-setup-protocol.md            Bluetooth setup protocol (app <-> Raspberry Pi)
```

Each screen lives in a folder under `features/`; anything shared lives in `core/`.

## Design system

Dark theme with a gold frame, colored orbs at the top and **Cinzel** headings
(through `google_fonts`; the font is downloaded the first time, so it needs
internet). There are no mana symbols or official game art, only generic fantasy
elements.

| Token (`MagicColors`) | Color | Use |
|---|---|---|
| `background` | `#0E0B1A` | Screen background |
| `surface` | `#1A1530` | Fields and panels |
| `gold` | `#D4AF37` | Main button, icons, links |
| `goldSoft` | gold at 50% | Borders and dividers |
| `parchment` | `#F3E6C4` | Main text |
| `danger` | `#E5645A` | Errors |

Always use `MagicColors` and `Theme.of(context)`; no loose colors in screens.

- **`MagicScaffold`:** base of every screen, with the dark background and orbs.
  Accepts a transparent `appBar` and a `drawer`.
- **`MagicPanel`:** dark purple panel with a gold border, to group content.
- **`MagicWordmark`:** the "CARDFORGE" title.
- **`CardEmblem`:** the fanned cards.
- **`MagicBackdrop`:** the orbs, with adjustable intensity.
- **`MagicSymbols`:** the row of five gold-framed symbols (sun, drop, skull, flame,
  tree), a PNG with a transparent background in `assets/images/`. Turn it on with
  `MagicScaffold(showSymbols: true)`; add `showOrbs: false` to replace the orbs.
  Today only **Cards in folders** uses it.

## Creating a new screen

1. Create `lib/features/<name>/<name>_screen.dart`.
2. Use `MagicScaffold` as the base and `MagicPanel` to group content.
3. Put every text in `AppStrings` (English and Portuguese).
4. Register the route in `lib/core/router/app_router.dart`.
5. Hook up the menu item in `home_screen.dart`.

## Testing

```bash
flutter test                   # run everything
flutter test test/features     # screens and logic only
flutter test --coverage        # writes coverage/lcov.info
```

Tests use the simulated backend, the simulated secure storage and
`SharedPreferences.setMockInitialValues`, so they need no server, emulator or
internet.

| Area | What it covers |
|---|---|
| `features/auth` | Session, token and "Remember me"; login validation and sign-in |
| `features/splash` | Destination by session, server error and "Try again" |
| `features/home` | Linked or not, side menu, bell badge, sign out |
| `features/machine` | Linking state machine (timeout, Wi-Fi errors, unreachable machine), BLE codec, the link screen end to end, saved machine and address, machine controller (events, alerts, reconnect), the control screen |
| `features/alerts` | Controller, list, read state, banner |
| `features/cards` | Models, API and simulated repositories, search/paging controller, the screen |
| `features/settings` | Language switch and persistence, alert simulator |
| `core/l10n` | Every enum value has text in both languages; language persistence |
| `core/widgets` | Design system components |

Use `pumpApp(tester, initialLocation: '/route')` to open a screen. Options:
`preferences` (e.g. `{'language_code': 'pt'}` or `linkedMachinePrefs`),
`linkService`, `machineApi` (a `FakeMachineApi` from `test/helpers`) and
`healthService` to swap the Bluetooth, machine or server services.

## Roadmap

- [x] Splash with server check
- [x] Authentication (username/email + password, Google, remember me)
- [x] English UI with a language setting (English/Portuguese)
- [x] Machine linking over Bluetooth (real BLE transport + simulated one)
- [x] Alerts: banner and alerts screen
- [x] Automated tests
- [x] Real Bluetooth transport (`docs/ble-setup-protocol.md`)
- [x] Machine control (Start, Stop, emergency stop, progress, diagnostics)
- [ ] Pause / resume (needs support in `sorter_api`)
- [x] Cards in folders (search and list from the server)
- [ ] CSV report
- [ ] Sorting rules
- [x] Alerts from the machine (WebSocket, while the app is open)
- [ ] Push notifications when the app is closed
- [ ] Sign-up and password recovery

## Dependencies

| Package | Use |
|---|---|
| `go_router` | Navigation |
| `http` | API calls |
| `google_sign_in` (6.x) | Google sign-in |
| `flutter_secure_storage` | Session token |
| `shared_preferences` | Language and linked machine |
| `google_fonts` | Cinzel headings |
| `flutter_blue_plus` | Bluetooth Low Energy for the machine setup. Free for personal/educational use (`License.nonprofit`); for-profit use needs a commercial license. It sends a small license ping at build time |
| `web_socket_channel` | Live events from the machine (`/ws/events`) |
| `flutter_localizations` | Material text in Portuguese |

Keep `google_sign_in` on 6.x: version 7 changed the API and needs changes in
`auth_service.dart`.
