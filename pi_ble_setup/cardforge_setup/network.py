"""Wi-Fi control (NetworkManager / nmcli) and network checks.

Raspberry Pi OS Bookworm and later manage Wi-Fi with NetworkManager, so this
uses `nmcli`. Older images (Bullseye, wpa_supplicant only) are not supported.

The connection the app creates is always named `cardforge-wifi`, so sending new
Wi-Fi details replaces it instead of piling up profiles.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import urllib.request
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

CONNECTION_NAME = "cardforge-wifi"

# Values of wifi.state / wifi.error in the protocol.
IDLE, CONNECTING, CONNECTED, FAILED = "idle", "connecting", "connected", "failed"
NETWORK_NOT_FOUND = "NETWORK_NOT_FOUND"
WRONG_PASSWORD = "WRONG_PASSWORD"
TIMEOUT = "TIMEOUT"
CONNECT_FAILED = "CONNECT_FAILED"

Runner = Callable[[Sequence[str], float], Awaitable[Tuple[int, str]]]


async def run_command(args: Sequence[str], timeout: float) -> Tuple[int, str]:
    """Runs a command without a shell. Returns (exit code, stdout + stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "timed out"
    return proc.returncode or 0, out.decode("utf-8", "replace")


@dataclass
class WifiStatus:
    state: str = IDLE
    ssid: Optional[str] = None
    error: Optional[str] = None

    def to_json(self) -> dict:
        return {"state": self.state, "ssid": self.ssid, "error": self.error}


class WifiManager:
    """Runs one connection attempt at a time in the background."""

    def __init__(self) -> None:
        self.status = WifiStatus()
        self._task: Optional[asyncio.Task] = None

    @property
    def busy(self) -> bool:
        return self.status.state == CONNECTING

    def start(self, ssid: str, password: str) -> bool:
        """Starts connecting. False if an attempt is already running."""
        if self.busy:
            return False
        self.status = WifiStatus(state=CONNECTING, ssid=ssid)
        self._task = asyncio.get_running_loop().create_task(self._run(ssid, password))
        return True

    async def wait(self) -> None:
        """Waits for the current attempt (used by tests)."""
        if self._task:
            await self._task

    async def _run(self, ssid: str, password: str) -> None:
        try:
            error = await self.connect(ssid, password)
        except Exception:  # never leave the state stuck on "connecting"
            log.exception("Wi-Fi connection crashed")
            error = CONNECT_FAILED
        self.status = WifiStatus(state=FAILED if error else CONNECTED, ssid=ssid, error=error)
        log.info("Wi-Fi %s: %s", ssid, error or "connected")

    async def connect(self, ssid: str, password: str) -> Optional[str]:
        """Returns None on success or one of the error codes."""
        raise NotImplementedError


def classify_nmcli_failure(code: int, output: str) -> str:
    text = output.lower()
    # nmcli exit codes: 3 = timeout, 4 = activation failed, 10 = not found.
    if code in (3, 124) or "timeout" in text or "timed out" in text:
        return TIMEOUT
    if code == 10 or "no network with ssid" in text:
        return NETWORK_NOT_FOUND
    if "secrets were required" in text or "psk" in text or "password" in text or "802-1x" in text:
        return WRONG_PASSWORD
    return CONNECT_FAILED


def parse_terse_ssids(output: str) -> List[str]:
    """Parses `nmcli -t -f SSID device wifi list` (terse mode escapes ':' and '\\')."""
    ssids = []
    for line in output.splitlines():
        ssid = line.replace("\\:", ":").replace("\\\\", "\\").strip()
        if ssid and ssid not in ssids:
            ssids.append(ssid)
    return ssids


def parse_active_ssid(output: str) -> Optional[str]:
    """Parses `nmcli -t -f ACTIVE,SSID device wifi list`: lines like `yes:Home`."""
    for line in output.splitlines():
        active, _, ssid = line.partition(":")
        if active.strip() == "yes":
            ssid = ssid.replace("\\:", ":").replace("\\\\", "\\").strip()
            return ssid or None
    return None


class NmcliWifi(WifiManager):
    def __init__(self, interface: str = "wlan0", connect_timeout: int = 45, runner: Runner = run_command) -> None:
        super().__init__()
        self.interface = interface
        self.connect_timeout = connect_timeout
        self._run_cmd = runner

    async def connect(self, ssid: str, password: str) -> Optional[str]:
        nm = ["nmcli"]
        await self._run_cmd(nm + ["radio", "wifi", "on"], 10)

        # Already on that network (set up with Raspberry Pi Imager, the desktop,
        # or an earlier link): reconnecting would only drop Wi-Fi, and on a Pi 3
        # the Bluetooth link with it, so report success right away.
        if await self.active_ssid() == ssid and await self.interface_ip():
            log.info("already connected to Wi-Fi %r: keeping the current connection", ssid)
            return None

        code, out = await self._run_cmd(
            nm + ["-t", "-f", "SSID", "device", "wifi", "list", "ifname", self.interface, "--rescan", "yes"], 30
        )
        visible = code == 0 and ssid in parse_terse_ssids(out)

        # Replace the profile from a previous attempt (it may hold a wrong password).
        await self._run_cmd(nm + ["connection", "delete", "id", CONNECTION_NAME], 10)

        args = nm + ["--wait", str(self.connect_timeout), "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        args += ["ifname", self.interface, "name", CONNECTION_NAME]
        if not visible:
            args += ["hidden", "yes"]  # not in the scan: maybe a hidden network
        code, out = await self._run_cmd(args, self.connect_timeout + 15)
        if code != 0:
            await self._run_cmd(nm + ["connection", "delete", "id", CONNECTION_NAME], 10)
            error = classify_nmcli_failure(code, out)
            if not visible and error == CONNECT_FAILED:
                error = NETWORK_NOT_FOUND
            log.warning("nmcli connect failed (%s): %s", code, out.strip())
            return error

        # Associated: wait a little for DHCP to hand out an address.
        for _ in range(20):
            if await self.interface_ip():
                return None
            await asyncio.sleep(0.5)
        return CONNECT_FAILED

    async def active_ssid(self) -> Optional[str]:
        """SSID the interface is connected to right now, if any."""
        code, out = await self._run_cmd(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "ifname", self.interface, "--rescan", "no"],
            10,
        )
        if code != 0:
            return None
        return parse_active_ssid(out)

    async def interface_ip(self) -> Optional[str]:
        code, out = await self._run_cmd(["nmcli", "-g", "IP4.ADDRESS", "device", "show", self.interface], 10)
        if code != 0:
            return None
        first = out.strip().split("|")[0].strip()
        return first.split("/")[0] or None


class SimulatedWifi(WifiManager):
    """For testing the Bluetooth side without touching the real network.

    Same rules as the app's simulated machine: a network named `fail` is not
    found, and the password `wrongpass` is rejected.
    """

    def __init__(self, delay: float = 3.0) -> None:
        super().__init__()
        self.delay = delay

    async def connect(self, ssid: str, password: str) -> Optional[str]:
        await asyncio.sleep(self.delay)
        if ssid.lower() == "fail":
            return NETWORK_NOT_FOUND
        if password == "wrongpass":
            return WRONG_PASSWORD
        return None


def wifi_readiness(interface: str = "wlan0", run=None) -> Tuple[bool, str]:
    """Checks that NetworkManager can drive `interface`. Returns (ok, message).

    Plain Debian without a desktop uses ifupdown + wpa_supplicant instead of
    NetworkManager; then the Wi-Fi command can't work (see README, "Plain Debian").
    """
    import shutil
    import subprocess

    def default_run(args: Sequence[str]) -> Tuple[int, str]:
        res = subprocess.run(list(args), capture_output=True, text=True, timeout=10)
        return res.returncode, res.stdout + res.stderr

    run = run or default_run
    if shutil.which("nmcli") is None and run is default_run:
        return False, "nmcli not found: install NetworkManager (sudo apt install network-manager)"
    try:
        code, out = run(["nmcli", "-t", "-f", "RUNNING", "general"])
        if code != 0 or out.strip() != "running":
            return False, "NetworkManager is not running (sudo systemctl enable --now NetworkManager)"
        code, out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not run nmcli: {e}"
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == interface:
            state = parts[2]
            if state.startswith("unmanaged"):
                return False, (
                    f"{interface} is not managed by NetworkManager: remove it from "
                    "/etc/network/interfaces (and interfaces.d/), then restart NetworkManager"
                )
            if state == "unavailable":
                return False, f"{interface} is unavailable: Wi-Fi blocked or firmware missing (rfkill list)"
            return True, f"{interface} managed by NetworkManager ({state})"
    return False, f"no {interface} device: Wi-Fi firmware missing? (firmware-brcm80211 on plain Debian)"


def current_ip() -> Optional[str]:
    """IPv4 address of the interface that has the default route, if any.

    Connecting a UDP socket sends no packet; it only picks the route.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        return None if ip.startswith("0.") else ip
    except OSError:
        return None
    finally:
        sock.close()


async def best_ip(wifi: WifiManager) -> Optional[str]:
    """The Wi-Fi address when there is one (that is the network the phone is on),
    else the address of whatever interface has the default route (e.g. Ethernet)."""
    if isinstance(wifi, NmcliWifi):
        ip = await wifi.interface_ip()
        if ip:
            return ip
    return current_ip()


async def api_is_up(port: int, timeout: float = 1.5) -> bool:
    """True if sorter_api answers GET /health on this machine."""

    def check() -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as res:
                return res.status == 200
        except Exception:
            return False

    return await asyncio.to_thread(check)