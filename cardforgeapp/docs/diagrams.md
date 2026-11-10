# CardForge: analysis diagrams

Diagrams of the CardForge app and the card-sorting machine. They are written in
Mermaid, so the code can be edited and versioned with the project. They render on
GitHub, GitLab and in VS Code (with the Markdown Preview Mermaid Support
extension); you can also paste the code into mermaid.live.

Mermaid has no UML activity or use-case symbols, so those diagrams are drawn as
flowcharts: a diamond is a decision, an oval is a use case, a rectangle is an
action.

## 1. System context

Who talks to whom. Dashed lines are communication still to be confirmed.
The Bluetooth setup protocol is in [ble-setup-protocol.md](ble-setup-protocol.md).

```mermaid
flowchart LR
  U(["User"]) --> APP["CardForge app (Flutter)"]
  APP -->|"HTTPS: sign-in, linking, reports"| SRV["Server (API)"]
  APP -->|"Bluetooth LE: first setup (password, Wi-Fi, IP)"| SETUP
  APP -->|"Wi-Fi: REST :8000 and WebSocket events"| PI
  APP -->|"OAuth"| G["Google Sign-In"]
  SRV -->|"validates the idToken"| G
  SRV -.->|"alerts: push or WebSocket (to confirm)"| APP
  subgraph MAC["Sorting machine"]
    SETUP["cardforge_setup (BLE, nmcli)"]
    PI["Raspberry Pi 3: sorter_api"]
    SETUP -.->|"joins Wi-Fi"| PI
    CAM["Camera"]
    ROL["Roller and folder mechanism"]
    PI --> CAM
    PI --> ROL
  end
  PI -.->|"events and sync (to confirm)"| SRV
```

## 2. Use cases

What each actor does with the system. The machine is an actor because it receives
commands and sends events.

```mermaid
flowchart LR
  U(["User"])
  G(["Google"])
  M(["Machine"])
  subgraph SYS["CardForge app"]
    UC1(["Sign in"])
    UC2(["Sign in with Google"])
    UC3(["Link machine"])
    UC4(["Send Wi-Fi details"])
    UC5(["Define sorting rules"])
    UC6(["Control the run"])
    UC7(["Browse cards in folders"])
    UC8(["Export CSV report"])
    UC9(["View alerts"])
    UC10(["Change language"])
  end
  U --- UC1 & UC3 & UC5 & UC6 & UC7 & UC8 & UC9 & UC10
  UC2 -.->|"extend"| UC1
  UC4 -.->|"extend"| UC3
  G --- UC2
  M --- UC3 & UC6 & UC9
```

## 3. Activity: startup and sign-in

Implemented flow: Splash, server check and sign-in with a password or Google,
with the option to remember the session.

```mermaid
flowchart TD
  A(["Open the app"]) --> B["Splash: GET /health"]
  B --> C{"Server answered?"}
  C -->|"no"| D["Show connection error"]
  D -->|"Try again"| B
  C -->|"yes"| E{"Saved token?"}
  E -->|"yes"| H(["Home"])
  E -->|"no"| F["Login screen"]
  F --> G{"Method"}
  G -->|"username or email and password"| V{"Valid fields?"}
  V -->|"no"| F
  V -->|"yes"| P["POST /auth/login"]
  G -->|"Google"| GO["Choose Google account"]
  GO --> PG["POST /auth/google with idToken"]
  P --> R{"Response"}
  PG --> R
  R -->|"401 or error"| ERR["Show message"]
  ERR --> F
  R -->|"200 with token"| L{"Remember me?"}
  L -->|"yes"| S1["Store token on the device"]
  L -->|"no"| S2["Keep token in memory only"]
  S1 --> H
  S2 --> H
```

## 4. Activity: link a machine

Implemented flow (Bluetooth is simulated for now): scan, pick a machine, machine
password, optional Wi-Fi details and an asynchronous confirmation.

```mermaid
flowchart TD
  A(["Home with no linked machine"]) --> B["Tap Link machine"]
  B --> C["Scan for machines over Bluetooth"]
  C --> D{"Machines found?"}
  D -->|"no"| D1["Show hint"]
  D1 -->|"Scan again"| C
  D -->|"yes"| E["User selects a machine"]
  E --> F["Enter the machine password"]
  F --> G{"Password accepted?"}
  G -->|"no"| G1["Show wrong password"]
  G1 --> F
  G -->|"yes"| H{"Send Wi-Fi details?"}
  H -->|"yes"| I["Enter network name and password"]
  I --> J["Send details to the machine"]
  J --> K["Wait for the machine to join the Wi-Fi"]
  H -->|"skip"| L["Link machine to the account"]
  K --> L
  L --> M{"Machine confirmed before timeout?"}
  M -->|"no or error"| N["Show failure"]
  N -->|"Try again"| C
  M -->|"yes"| O["Save machine and raise alerts"]
  O --> P(["Home with the machine and its status"])
```

## 5. Activity: card sorting cycle

The physical process, card by card, with the exceptions that raise an alert in the
app.

```mermaid
flowchart TD
  A(["Start run"]) --> B["Roller pulls one card"]
  B --> C{"Normal feed?"}
  C -->|"jam or two cards"| X["Stop and alert the user"]
  C -->|"yes"| D["Camera captures the card"]
  D --> E{"Card identified?"}
  E -->|"no or face down"| F["Send to the unidentified folder and alert"]
  E -->|"yes"| G["Apply the sorting rules"]
  G --> H["Choose the destination folder"]
  H --> I["Place the card in the folder"]
  F --> J["Record the card in the inventory"]
  I --> J
  J --> K{"More cards?"}
  K -->|"yes"| B
  K -->|"no"| Z(["Done: sorting finished alert, inventory and report available"])
  X --> W{"User fixed it and resumed?"}
  W -->|"yes"| B
  W -->|"no"| Y(["Run interrupted"])
```

## 6. Run states

Life cycle of a machine run, as the app sees it.

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> Running: start
  Running --> Paused: pause
  Paused --> Running: resume
  Running --> Error: jam or double feed
  Error --> Running: fixed and resume
  Running --> ConnectionLost: Wi-Fi dropped
  ConnectionLost --> Running: reconnected and resume
  Running --> Finished: no more cards
  Running --> Stopped: stop
  Paused --> Stopped: stop
  Error --> Stopped: stop
  Finished --> Idle
  Stopped --> Idle
```

## 7. Sequence: Google sign-in

Messages between the app, Google and the server. This is the flow implemented in
`AuthService`.

```mermaid
sequenceDiagram
  actor U as User
  participant A as App
  participant G as Google
  participant S as Server
  U->>A: Taps Continue with Google
  A->>G: Requests sign-in
  G-->>A: idToken
  A->>S: POST /auth/google with id_token
  S->>G: Validates the token
  G-->>S: Token valid
  S-->>A: 200 with access_token
  A->>A: Stores the token
  A-->>U: Opens Home
```

## 8. Sequence: link the machine (simplified)

Three actors: smartphone, machine and server. The status coming back through the
server is an assumption.

```mermaid
sequenceDiagram
  participant P as Smartphone
  participant M as Machine
  participant S as Server
  P->>M: Scan and connect (Bluetooth)
  M-->>P: Machine found
  P->>M: Machine password
  M-->>P: Password accepted
  Note over P: Asks: send Wi-Fi details?
  P->>M: Network name and password
  Note over M: Joins the Wi-Fi
  M->>S: Registers as online
  S-->>M: Registration confirmed
  S-->>P: Status: machine connected
  Note over P: Machine linked, waiting for Start
```

## 9. Sequence: sorting run (simplified)

After Start, the machine repeats the cycle for each card. Routing the Start
command through the server is an assumption.

```mermaid
sequenceDiagram
  participant P as Smartphone
  participant S as Server
  participant M as Machine
  P->>S: Start with the sorting rules
  S->>M: Command: start sorting
  loop For each card
    Note over M: Pull, identify, apply the rule, place in folder
    M->>S: Records the card and its folder
    S-->>P: Progress and alerts
  end
  M->>S: Run finished
  S-->>P: Sorting finished alert, inventory and CSV ready
  Note over M: Waits for the next Start
```

## 10. Sequence: alerts

How an event reaches the user. The server channel (push, WebSocket or polling) is
still to be decided; the app side is implemented (`AlertsController.add`).

```mermaid
sequenceDiagram
  participant M as Machine
  participant S as Server
  participant A as App
  actor U as User
  M->>S: Event (jam, Wi-Fi lost, sorting finished...)
  S-->>A: Alert
  A->>A: Adds it to the alerts list
  A-->>U: Banner over the current screen
  U->>A: Taps View
  A-->>U: Alerts screen, newest first
```

## 11. Data model (proposal)

Conceptual entities for the server and the inventory. A starting point to validate
with the backend.

```mermaid
erDiagram
  USER ||--o{ MACHINE : owns
  USER ||--o{ RULE : defines
  MACHINE ||--o{ FOLDER : contains
  MACHINE ||--o{ RUN : performs
  RULE ||--o{ RUN : "applied in"
  RUN ||--o{ SCANNED_CARD : records
  RUN ||--o{ ALERT : raises
  FOLDER ||--o{ SCANNED_CARD : receives
  USER {
    string id PK
    string name
    string email
    string provider
  }
  MACHINE {
    string id PK
    string nickname
    string state
  }
  RULE {
    string id PK
    string name
    string criteria
  }
  RUN {
    string id PK
    datetime started_at
    datetime ended_at
    string state
  }
  FOLDER {
    string id PK
    string name
    int position
  }
  SCANNED_CARD {
    string id PK
    string card_name
    string status
  }
  ALERT {
    string id PK
    string type
    datetime created_at
  }
```

## 12. Sequence: search cards in folders

How the Cards in folders screen gets its data. Nothing typed lists every card;
typing a name lists the matching cards and the folders each one is in.

```mermaid
sequenceDiagram
  actor U as User
  participant A as App
  participant S as Server
  U->>A: Opens Cards in folders
  A->>S: GET /cards (no query)
  S-->>A: Cards with their folders (1 to 6) and total
  A-->>U: List of cards in the folders
  U->>A: Types a card name
  Note over A: Waits for a short pause in typing
  A->>S: GET /cards?query=name
  S-->>A: Matching cards with their folders
  A-->>U: Each match with the folders it is in
  U->>A: Scrolls to the end of the list
  A->>S: GET /cards?query=name&offset=30
  S-->>A: Next page
```

## Assumptions and open points

- During a run the app may talk directly to the Raspberry over local Wi-Fi, or
  everything may go through the server. It is dashed in diagram 1 until
  confirmed.
- The real Bluetooth protocol (BLE or classic, service identifiers and message
  format) is not defined yet, so the app uses a simulated machine.
- Unidentified or face-down cards go to a folder of their own and raise an alert.
- The inventory and the report are only available after the run finishes.
- Losing the connection pauses the run, and the user resumes it from the app.
- The data model in diagram 11 is an initial proposal, not yet validated.

## Machine setup (Bluetooth, then Wi-Fi)

What happens when the user links a machine. Details in
[ble-setup-protocol.md](ble-setup-protocol.md).

```mermaid
sequenceDiagram
  actor U as User
  participant A as App
  participant S as Pi: cardforge_setup (BLE)
  participant N as Pi: NetworkManager
  participant R as Pi: sorter_api (:8000)
  U->>A: Link machine
  A->>S: scan by service UUID, connect
  A->>S: info
  S-->>A: name, protocol 1
  U->>A: machine password
  A->>S: auth {password}
  S-->>A: session (or WRONG_PASSWORD / LOCKED)
  U->>A: Wi-Fi name and password
  A->>S: wifi {session, ssid, password}
  S->>N: nmcli device wifi connect
  loop every 1.5 s
    A->>S: status
    S-->>A: wifi.state connecting / connected / failed
  end
  S-->>A: ip, api_port, api_ok
  A->>A: disconnect Bluetooth
  A->>R: GET /health over Wi-Fi
  R-->>A: 200
  A->>A: save machine (name, BLE id, ip, port)
  A->>R: WS /ws/events (stays open)
```

