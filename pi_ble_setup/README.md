# CardForge Bluetooth setup service (Raspberry Pi)

Lets the CardForge app set up a new machine over Bluetooth Low Energy:

1. The phone finds the machine (`CardForge-XXXX`) and checks the **machine password**.
2. The phone sends the **Wi-Fi name and password**. The Pi joins the network with NetworkManager.
3. The Pi tells the phone its **IP address**. From then on the app uses the REST
   API (`sorter_api`) at `http://<ip>:8000`.

This runs **next to** `sorter_api`, as its own systemd service. It does not talk to the Arduinos.
The protocol is in [`docs/ble-setup-protocol.md`](docs/ble-setup-protocol.md).

## Requirements

* Raspberry Pi 3 or newer, with Wi-Fi managed by **NetworkManager** (`nmcli`):
  * **Raspberry Pi OS Bookworm or Trixie** (Trixie is based on Debian 13): works as is.
  * **Plain Debian 13** (raspi.debian.net image, or a netinst without a desktop): uses
    ifupdown + wpa_supplicant by default, so install NetworkManager first (see below).
  * `sudo venv/bin/python -m cardforge_setup info` tells you on the last line whether
    Wi-Fi setup can work on this system.
* BlueZ 5.50+ (Debian 13 ships 5.82), Python 3.9+ (Debian 13 ships 3.13).
* `sorter_api` must listen on **all interfaces** (`0.0.0.0:8000`), not only `127.0.0.1`,
  so the phone can reach it over Wi-Fi.

## Install

```bash
sudo apt install -y python3-venv bluez network-manager
sudo mkdir -p /opt/cardforge && sudo cp -r pi_ble_setup /opt/cardforge/
cd /opt/cardforge/pi_ble_setup
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

# The password the app asks for in "Machine password". Put it on a label on the machine.
sudo venv/bin/python -m cardforge_setup set-password
sudo venv/bin/python -m cardforge_setup info     # name (e.g. CardForge-C3D4) and a Wi-Fi check

sudo cp cardforge-setup.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cardforge-setup
journalctl -u cardforge-setup -f                 # logs (never contain passwords)
```

## Plain Debian 13 (not Raspberry Pi OS)

`/etc/debian_version` says `13.x` on **both** Raspberry Pi OS Trixie and plain Debian, so
check which one you have: `ls /etc/apt/sources.list.d/` lists a `raspi` file only on
Raspberry Pi OS, and `systemctl is-active NetworkManager` says `active` when nothing below
is needed.

On plain Debian, do this **with a keyboard and screen or Ethernet attached** (the Wi-Fi
link you are using may drop while it is handed over):

```bash
sudo apt install -y network-manager bluez bluez-firmware firmware-brcm80211
# Hand wlan0 to NetworkManager: delete or comment out every "wlan0" stanza in
# /etc/network/interfaces and /etc/network/interfaces.d/* (NetworkManager ignores
# interfaces that ifupdown manages). Keep eth0 there if you want.
sudo systemctl enable --now NetworkManager
sudo systemctl restart NetworkManager
nmcli device                      # wlan0 must show "disconnected" or "connected", not "unmanaged"
bluetoothctl show                 # must list a controller with "Powered: yes"
```

(`bluez-firmware` and `firmware-brcm80211` are in Debian's `non-free-firmware`
component, which the Debian Raspberry Pi images enable.)

**Pi 3 serial port:** on the Pi 3 the Bluetooth chip uses the main UART. If the RS-485
adapter for the Arduinos is on the GPIO pins and you turned Bluetooth off to get that
UART (`dtoverlay=disable-bt`), the setup service can't advertise. A USB-RS485 adapter
avoids the conflict.

## Testing

* **Logic tests, on any computer** (no Bluetooth needed):
  `python -m pytest tests` (install `pytest` first).
* **Bluetooth without touching the Wi-Fi:** stop the service and run
  `sudo venv/bin/python -m cardforge_setup run --simulate-wifi -v`.
  It pretends to connect: the network `fail` is "not found" and the password
  `wrongpass` is rejected.
* **On a Mac (no Pi needed):** bless uses CoreBluetooth there, so the Mac can play the
  machine for the phone. No `sudo`; keep the config in the folder:

  ```bash
  python3 -m venv venv && venv/bin/pip install -r requirements.txt
  venv/bin/python -m cardforge_setup --config ./setup.json set-password
  venv/bin/python -m cardforge_setup --config ./setup.json run --simulate-wifi -v
  ```

  Allow Terminal to use Bluetooth when macOS asks (System Settings > Privacy & Security >
  Bluetooth). The machine shows as `CardForge-0000` (a Mac has no Pi serial; set `"name"`
  in `setup.json` to change it). It reports the Mac's IP, so run
  `python -m sorter_api --simulate` on the same Mac. A Linux laptop with BlueZ works the same way.
* **From a computer instead of the phone:** the free *nRF Connect* app (Android/iOS)
  can connect, enable notifications on TX and write
  `{"id":1,"cmd":"info"}` followed by a newline (`0A`) to RX.

## Troubleshooting

| Symptom | Check |
|---|---|
| The app finds no machine | `systemctl status cardforge-setup`; `bluetoothctl show` must say `Powered: yes`. Bluetooth on the phone on, location/nearby-devices permission granted |
| `Could not locate bluetooth adapter` | `sudo rfkill unblock bluetooth`, then restart the service. On plain Debian also install `bluez-firmware` |
| Log says `Wi-Fi setup will fail: ...` | Follow the message; on plain Debian see "Plain Debian 13" above |
| Wi-Fi fails with "network not found" | The Pi 3 Model B only sees **2.4 GHz** networks. Hidden networks are tried automatically |
| Wi-Fi connects but the app can't reach the machine | Phone and Pi on the same network? Some guest / campus networks isolate clients: use a phone hotspot or a home router for the demo. Is `sorter_api` bound to `0.0.0.0`? |
| SSH dropped during the test | Expected if you were connected over the Wi-Fi the Pi just left |

## Files

| File | What it does |
|---|---|
| `cardforge_setup/framing.py` | Splits/joins messages into BLE-sized chunks |
| `cardforge_setup/protocol.py` | The commands (`info`, `auth`, `wifi`, `status`) |
| `cardforge_setup/auth.py` | Password hash, lockout after 5 wrong tries, 10-minute sessions |
| `cardforge_setup/network.py` | `nmcli` Wi-Fi connection, IP lookup, `sorter_api` health check |
| `cardforge_setup/ble_server.py` | GATT server with `bless` (BlueZ over D-Bus) |
| `cardforge_setup/__main__.py` | Command line (`set-password`, `info`, `run`) |
