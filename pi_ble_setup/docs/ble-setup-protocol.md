# BLE setup protocol: app ↔ Raspberry Pi

The app uses Bluetooth Low Energy **only for the first setup**: check the
machine password, send the Wi-Fi details and learn the machine's IP address.
After that, everything goes through the REST API on Wi-Fi
(`http://<ip>:8000`, see `rest-api.md`).

Pi side: `cardforge_setup` (this folder). App side:
`lib/features/machine/ble/` in CardForgeApp. Change both together.

## 1. GATT service

| Item | UUID | Properties |
|---|---|---|
| Service "CardForge setup" | `cdaa0001-bc5d-45ab-8926-3aa83f2ab5c0` | advertised |
| RX: app → Pi | `cdaa0002-bc5d-45ab-8926-3aa83f2ab5c0` | write, write without response |
| TX: Pi → app | `cdaa0003-bc5d-45ab-8926-3aa83f2ab5c0` | notify (and read) |

* The Pi advertises the service UUID and the name `CardForge-XXXX`, where XXXX
  is the end of the Pi's CPU serial. The app scans **by service UUID**, so the
  name is only for display.
* It works like a serial port: the app writes requests to RX and subscribes
  to TX to get the replies.

## 2. Framing

* Every message is a UTF-8 **JSON object followed by `\n`**. JSON never has a
  raw newline inside, so `\n` always ends a message.
* A message can be split into chunks of any size. The receiver keeps adding
  bytes until it sees `\n`.
* The **app** writes chunks of `MTU − 3` bytes (Android asks for MTU 512, iOS
  negotiates on its own). Long writes (prepared writes) are **not** used,
  because BlueZ through `bless` ignores their offset.
* The **Pi** notifies in chunks of **20 bytes**, which fit in the minimum MTU (23).
* Max message size: **1024 bytes**. A partial message that gets no bytes for
  2 s is thrown away (the phone disconnected halfway).

## 3. Requests and replies

```
→ {"id": 7, "cmd": "auth", "password": "1234"}
← {"id": 7, "ok": true, "session": "9f2c…", "expires_in": 600}
← {"id": 7, "ok": false, "error": "WRONG_PASSWORD", "attempts_left": 4}
```

* `id`: an integer chosen by the app. The reply carries the same `id`. It is
  `null` only when the request could not be read (`BAD_JSON`, `TOO_LONG`).
* The app sends one request at a time and waits for its reply (timeout 10 s,
  25 s for `auth`).

| `cmd` | Arguments | Needs session | Reply (`ok: true`) |
|---|---|---|---|
| `ping` | none | no | `{}` |
| `info` | none | no | `protocol` (1), `machine_id`, `name`, `api_port` |
| `auth` | `password` | no | `session`, `expires_in` (s) |
| `wifi` | `session`, `ssid`, `password` (`""` = open network) | **yes** | `state: "connecting"`. Connection runs in the background |
| `status` | none | no | `wifi`, `ip`, `api_port`, `api_ok` (see below) |

`status` reply:

```json
{ "id": 5, "ok": true,
  "wifi": { "state": "connected", "ssid": "Home", "error": null },
  "ip": "192.168.0.42", "api_port": 8000, "api_ok": true }
```

| Field | Meaning |
|---|---|
| `wifi.state` | `idle` (nothing sent since boot) · `connecting` · `connected` · `failed` |
| `wifi.error` | When `failed`: `NETWORK_NOT_FOUND`, `WRONG_PASSWORD`, `TIMEOUT`, `CONNECT_FAILED` |
| `ip` | The Pi's IPv4 address on Wi-Fi (or on the default route, e.g. Ethernet). `null` = no network |
| `api_ok` | `true` if `sorter_api` answers `GET /health` on the Pi itself |

`status` needs no password, so the app can find the machine's new IP later
("Find over Bluetooth") without asking for the password again.

## 4. Errors

| `error` | When |
|---|---|
| `BAD_JSON` | Not a JSON object |
| `TOO_LONG` | Message over 1024 bytes |
| `BAD_CMD` | Unknown `cmd` |
| `BAD_ARG` | Missing or invalid argument. `field` says which. SSID 1–32 bytes; password empty, 8–63 characters or 64 hex digits |
| `WRONG_PASSWORD` | Wrong machine password. `attempts_left` |
| `LOCKED` | 5 wrong passwords in a row: every attempt is refused for 60 s. `retry_after` (s) |
| `NOT_AUTHENTICATED` | `wifi` without a valid session (they last 10 min) |
| `BUSY` | `wifi` while a previous connection attempt is still running |
| `INTERNAL` | Bug on the Pi (see `journalctl -u cardforge-setup`) |

## 5. Setup sequence (what the app does)

```
scan (service UUID) → connect → subscribe TX
→ info                                    ← protocol 1 (else "different setup version")
→ auth {password}                         ← session
→ wifi {session, ssid, password}          ← state: connecting     (skipped if the user skips Wi-Fi)
→ status   every 1.5 s until wifi.state is connected or failed
→ status                                  ← ip, api_port, api_ok
disconnect BLE
GET http://<ip>:<api_port>/health over Wi-Fi  → machine saved (name, BLE id, ip, port)
```

* If the Pi's Bluetooth drops while it changes network (the Pi 3 shares one
  chip for both), the app reconnects up to 3 times and goes on polling `status`.
* If the phone cannot reach `/health` (phone on another network, mobile data,
  or a network that isolates clients), the link fails with "can't reach the
  machine over Wi-Fi".

## 6. Security notes

* The password is stored on the Pi as a PBKDF2-SHA256 hash (`/etc/cardforge/setup.json`, mode 600).
* BLE traffic is **not encrypted** (no pairing), so someone nearby with a BLE
  sniffer could capture the Wi-Fi password during setup. That is acceptable for
  the course demo. To harden it later, require an encrypted characteristic
  (`GATTAttributePermissions.write_encryption_required`), which makes the phone pair first.
* The REST API on port 8000 has no authentication: anyone on the same Wi-Fi
  can send commands. Keep the machine on a trusted network.
