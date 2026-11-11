"""RGB status LED on the Raspberry Pi GPIO (RF16, RF32, RF37, RF41 — Requirements, Appendix B).

Wiring (common cathode):  COM -> pin 9 (GND), RED -> pin 11 (GPIO 17),
                          BLUE -> pin 13 (GPIO 27), GREEN -> pin 15 (GPIO 22).
Settings: MTG_LED_PINS = "17,22,27" (BCM numbers, in R,G,B order), MTG_LED = auto | gpio | off.

    Mode              Pattern                  When
    OFF               off                      service stopped
    IDLE              solid green              ready, no run
    RUNNING           green, 1 Hz              run in progress
    FEED_JAM          solid red                run paused by a fault (jam, stuck card, base...);
                                               also module offline (Appendix C: Data-Link Loss)
    EMERGENCY_STOP    red, 4 Hz                (the E-stop cuts the Pi's power too: in practice the LED is off)
    DOUBLE_FEED       red, 2 Hz                RF06 not built: never sent
    STARTUP_BLOCKED   solid yellow             Start refused (bin not empty, no cards...) until the next command
    PAUSED_CONN       yellow, 1 Hz             RF30 does not apply (identification is offline): never sent
    SETUP             solid blue               no Wi-Fi configured yet (Bluetooth setup, RF40)
    NO_NETWORK        blue, 1 Hz               Wi-Fi configured but not connected

The machine state (set by the run controller) wins over the network state, which only
shows while the machine is idle.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

GREEN, RED, BLUE, OFF_COLOR = (0, 1, 0), (1, 0, 0), (0, 0, 1), (0, 0, 0)
YELLOW = (1, 0.55, 0)           # red + less green: pure R+G looks orange-green on most RGB LEDs

# mode -> (color, blink frequency in Hz or None for solid)
PATTERNS: dict[str, tuple[tuple[float, float, float], Optional[float]]] = {
    "OFF": (OFF_COLOR, None),
    "IDLE": (GREEN, None),
    "RUNNING": (GREEN, 1.0),
    "FEED_JAM": (RED, None),
    "EMERGENCY_STOP": (RED, 4.0),
    "DOUBLE_FEED": (RED, 2.0),
    "STARTUP_BLOCKED": (YELLOW, None),
    "PAUSED_CONN": (YELLOW, 1.0),
    "SETUP": (BLUE, None),
    "NO_NETWORK": (BLUE, 1.0),
}
LED_MODES = tuple(PATTERNS)

# Network states reported by NetworkMonitor
NET_OK, NET_SETUP, NET_DOWN = "ok", "setup", "down"


class MemoryLedDriver:
    """No hardware: remembers the last pattern (simulator, tests, PC)."""

    def __init__(self) -> None:
        self.color = OFF_COLOR
        self.blink_hz: Optional[float] = None

    def show(self, color, blink_hz: Optional[float]) -> None:
        self.color, self.blink_hz = color, blink_hz

    def close(self) -> None:
        pass


class GpioLedDriver:
    """gpiozero RGBLED, common cathode (HIGH = on), PWM so yellow can be mixed."""

    def __init__(self, pins: tuple[int, int, int]) -> None:
        from gpiozero import RGBLED  # Raspberry Pi only (apt: python3-gpiozero)

        r, g, b = pins
        self.led = RGBLED(red=r, green=g, blue=b, active_high=True, pwm=True)

    def show(self, color, blink_hz: Optional[float]) -> None:
        if blink_hz:
            half = 0.5 / blink_hz            # 50 % duty cycle
            self.led.blink(on_time=half, off_time=half, on_color=color, off_color=OFF_COLOR, background=True)
        else:
            self.led.color = color

    def close(self) -> None:
        self.led.off()
        self.led.close()


class StatusLed:
    def __init__(self, driver) -> None:
        self.driver = driver
        self._lock = threading.Lock()
        self._activity = "IDLE"              # set by the run controller
        self._network = NET_OK               # set by NetworkMonitor
        self.mode: Optional[str] = None      # what the LED shows now
        self.history: list[str] = []         # every mode shown (diagnostics, tests)
        self._refresh()

    def set(self, mode: str) -> None:
        """Machine state from the run controller (IDLE, RUNNING, FEED_JAM, STARTUP_BLOCKED...)."""
        if mode not in PATTERNS:
            raise ValueError(f"unknown LED mode: {mode}")
        with self._lock:
            self._activity = mode
            self._refresh()

    def set_network(self, state: str) -> None:
        with self._lock:
            self._network = state
            self._refresh()

    def _refresh(self) -> None:
        mode = self._activity
        if mode == "IDLE" and self._network != NET_OK:
            mode = "SETUP" if self._network == NET_SETUP else "NO_NETWORK"
        if mode == self.mode:
            return
        self.mode = mode
        self.history.append(mode)
        color, hz = PATTERNS[mode]
        try:
            self.driver.show(color, hz)
        except Exception as exc:              # never stop the machine because of the LED
            log.warning("status LED: %s", exc)

    def close(self) -> None:
        with self._lock:
            try:
                self.driver.show(OFF_COLOR, None)
                self.driver.close()
            except Exception as exc:
                log.warning("status LED: %s", exc)


def build_status_led(setting: str, pins: str, simulate: bool) -> StatusLed:
    """MTG_LED: gpio = the LED on the Pi; off = no LED; auto = gpio on the Pi, memory elsewhere."""
    if setting == "off" or (setting == "auto" and simulate):
        return StatusLed(MemoryLedDriver())
    try:
        rgb = tuple(int(p) for p in pins.split(","))
        if len(rgb) != 3:
            raise ValueError("MTG_LED_PINS needs 3 BCM numbers: R,G,B")
        return StatusLed(GpioLedDriver(rgb))
    except Exception as exc:
        if setting == "gpio":
            raise
        log.warning("status LED disabled (%s): MTG_LED=auto, no GPIO here", exc)
        return StatusLed(MemoryLedDriver())


# --------------------------------------------------------------------------- network state

def nmcli_network_state(run: Callable = subprocess.run) -> str:
    """NET_OK when any interface is connected; NET_SETUP when no Wi-Fi was ever configured;
    NET_DOWN when Wi-Fi is configured but not connected. Uses NetworkManager (as pi_ble_setup)."""
    devices = run(["nmcli", "-t", "-f", "TYPE,STATE", "device"], capture_output=True, text=True, timeout=5).stdout
    for line in devices.splitlines():
        kind, _, state = line.partition(":")
        if kind in ("wifi", "ethernet") and state.startswith("connected") and "externally" not in state:
            return NET_OK
    saved = run(["nmcli", "-t", "-f", "TYPE", "connection", "show"], capture_output=True, text=True, timeout=5).stdout
    has_wifi = any(t.strip() in ("802-11-wireless", "wifi") for t in saved.splitlines())
    return NET_DOWN if has_wifi else NET_SETUP


class NetworkMonitor:
    """Polls the network state every `interval_s` and shows it on the LED while the machine is idle."""

    def __init__(self, led: StatusLed, interval_s: float = 5.0, probe: Callable[[], str] = nmcli_network_state):
        self.led = led
        self.interval_s = interval_s
        self.probe = probe
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def available() -> bool:
        return shutil.which("nmcli") is not None

    def check_once(self) -> str:
        try:
            state = self.probe()
        except Exception as exc:
            log.debug("network probe failed: %s", exc)
            state = NET_OK                    # unknown: do not show a false network problem
        self.led.set_network(state)
        return state

    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                self.check_once()
                self._stop.wait(self.interval_s)

        self._thread = threading.Thread(target=loop, name="network-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
