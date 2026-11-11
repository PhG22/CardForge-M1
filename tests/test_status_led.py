"""RGB status LED on the Pi GPIO (Appendix B) and the network state behind the blue modes (RF41)."""

import subprocess
import sys
import time
import types

from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.status_led import (BLUE, GREEN, NET_DOWN, NET_OK, NET_SETUP, RED, YELLOW, MemoryLedDriver,
                                   NetworkMonitor, StatusLed, build_status_led, nmcli_network_state)
from test_integration import LOW, ScriptedVision, fast_settings, wait_state


def test_patterns_follow_appendix_b():
    driver = MemoryLedDriver()
    led = StatusLed(driver)
    expected = {"IDLE": (GREEN, None), "RUNNING": (GREEN, 1.0), "FEED_JAM": (RED, None),
                "EMERGENCY_STOP": (RED, 4.0), "DOUBLE_FEED": (RED, 2.0), "STARTUP_BLOCKED": (YELLOW, None),
                "PAUSED_CONN": (YELLOW, 1.0), "SETUP": (BLUE, None), "NO_NETWORK": (BLUE, 1.0)}
    for mode, (color, hz) in expected.items():
        led.set(mode)
        assert (driver.color, driver.blink_hz) == (color, hz), mode


def test_network_state_shows_only_while_idle():
    led = StatusLed(MemoryLedDriver())
    led.set_network(NET_SETUP)
    assert led.mode == "SETUP"
    led.set("RUNNING")                       # the machine state wins
    assert led.mode == "RUNNING"
    led.set_network(NET_DOWN)
    assert led.mode == "RUNNING"
    led.set("IDLE")
    assert led.mode == "NO_NETWORK"
    led.set_network(NET_OK)
    assert led.mode == "IDLE"


class FakeRGBLED:
    instances = []

    def __init__(self, red, green, blue, active_high, pwm):
        self.pins, self.active_high, self.pwm = (red, green, blue), active_high, pwm
        self.color, self.blinking = None, None
        FakeRGBLED.instances.append(self)

    def blink(self, on_time, off_time, on_color, off_color, background):
        self.blinking = (on_time, off_time, on_color, off_color, background)

    def off(self):
        self.color = (0, 0, 0)

    def close(self):
        pass


def test_gpio_driver_uses_the_wired_pins(monkeypatch):
    monkeypatch.setitem(sys.modules, "gpiozero", types.SimpleNamespace(RGBLED=FakeRGBLED))
    led = build_status_led("gpio", "17,22,27", simulate=False)
    rgb = FakeRGBLED.instances[-1]
    assert rgb.pins == (17, 22, 27) and rgb.active_high is True and rgb.pwm is True   # common cathode
    assert rgb.color == GREEN                                                         # IDLE at start
    led.set("RUNNING")
    assert rgb.blinking == (0.5, 0.5, GREEN, (0, 0, 0), True)                         # 1 Hz
    led.set("EMERGENCY_STOP")
    assert rgb.blinking[:2] == (0.125, 0.125)                                         # 4 Hz
    led.close()
    assert rgb.color == (0, 0, 0)


def test_auto_without_gpio_falls_back_to_memory(monkeypatch):
    monkeypatch.setitem(sys.modules, "gpiozero", None)                # import fails, like on a PC
    assert isinstance(build_status_led("auto", "17,22,27", simulate=False).driver, MemoryLedDriver)


def nmcli(devices, connections):
    def run(cmd, **kw):
        out = devices if "device" in cmd else connections
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    return run


def test_network_state_from_nmcli():
    lo = "loopback:connected (externally)\n"
    assert nmcli_network_state(nmcli(lo + "wifi:connected\n", "")) == NET_OK
    assert nmcli_network_state(nmcli(lo + "ethernet:connected\nwifi:disconnected\n", "")) == NET_OK
    assert nmcli_network_state(nmcli(lo + "wifi:disconnected\n", "loopback\n")) == NET_SETUP
    assert nmcli_network_state(nmcli(lo + "wifi:connecting\n", "802-11-wireless\nloopback\n")) == NET_DOWN


def test_monitor_does_not_show_a_problem_it_could_not_check():
    led = StatusLed(MemoryLedDriver())

    def broken():
        raise FileNotFoundError("nmcli")
    assert NetworkMonitor(led, probe=broken).check_once() == NET_OK and led.mode == "IDLE"


# ------------------------------------------------------------------ driven by the main loop

def test_led_follows_the_run():
    vision = ScriptedVision([LOW] * 9)
    with TestClient(create_app(fast_settings(), vision=vision)) as c:
        led = c.app.state.status_led
        assert led.mode == "IDLE"
        c.put("/sim/state", json={"trays_with_cards": [2]})
        assert c.post("/run/start").status_code == 409
        assert led.mode == "STARTUP_BLOCKED"                     # yellow until the next command
        c.put("/sim/state", json={"trays_with_cards": []})
        assert c.post("/run/start").status_code == 200
        wait_state(c, "idle")
        assert led.history[-3:] == ["IDLE", "RUNNING", "IDLE"]


def test_led_turns_red_on_a_fault_and_back_on_resume():
    with TestClient(create_app(fast_settings(), vision=ScriptedVision([LOW] * 9))) as c:
        led = c.app.state.status_led
        c.put("/sim/state", json={"top_jammed": True})
        c.post("/run/start")
        wait_state(c, "paused")
        assert led.mode == "FEED_JAM"
        c.put("/sim/state", json={"top_jammed": False})
        c.post("/run/resume")
        time.sleep(0.05)
        wait_state(c, "idle")
        assert "RUNNING" in led.history[led.history.index("FEED_JAM"):] and led.mode == "IDLE"
