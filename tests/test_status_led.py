"""RGB status LED on the Pi GPIO (Appendix B) and the network state behind the blue modes (RF41)."""

import subprocess
import sys
import time
import types

from fastapi.testclient import TestClient

from sorter_api.api import create_app
from sorter_api.status_led import (BLUE, GREEN, NET_DOWN, NET_OK, NET_SETUP, RED, WHITE, YELLOW, MemoryLedDriver,
                                   NetworkMonitor, StatusLed, build_status_led, nmcli_network_state)
from test_integration import LOW, ScriptedVision, fast_settings, wait_state


def ready_led(driver=None) -> StatusLed:
    led = StatusLed(driver or MemoryLedDriver())
    led.ready()
    return led


def test_patterns_follow_appendix_b():
    driver = MemoryLedDriver()
    led = ready_led(driver)
    expected = {"IDLE": (GREEN, None), "RUNNING": (GREEN, 1.0), "FEED_JAM": (RED, None),
                "EMERGENCY_STOP": (RED, 4.0), "DOUBLE_FEED": (RED, 2.0), "STARTUP_BLOCKED": (YELLOW, None),
                "PAUSED_CONN": (YELLOW, 1.0), "SETUP": (BLUE, None), "NO_NETWORK": (BLUE, 1.0)}
    for mode, (color, hz) in expected.items():
        led.set(mode)
        assert (driver.color, driver.blink_hz) == (color, hz), mode


def test_starting_until_ready():
    """Power-on: white (Starting) whatever the machine and network say, until the service is up."""
    driver = MemoryLedDriver()
    led = StatusLed(driver)
    assert led.mode == "STARTING" and (driver.color, driver.blink_hz) == (WHITE, None)
    led.set("IDLE")
    led.set_network(NET_SETUP)
    assert led.mode == "STARTING" and led.history == ["STARTING"]      # no green/blue flash while booting
    led.ready()
    assert led.mode == "SETUP"                                           # ready to pair over Bluetooth
    led.set_network(NET_DOWN)
    assert led.mode == "NO_NETWORK"
    led.set_network(NET_OK)
    assert led.mode == "IDLE" and led.history == ["STARTING", "SETUP", "NO_NETWORK", "IDLE"]


def test_network_state_shows_only_while_idle():
    led = ready_led()
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
    assert rgb.color == WHITE                                                         # STARTING at start
    led.ready()
    assert rgb.color == GREEN                                                         # then IDLE
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
    # Wi-Fi brought up outside NetworkManager is still a connection (no false blue)
    assert nmcli_network_state(nmcli(lo + "wifi:connected (externally)\n", "loopback\n")) == NET_OK


def test_monitor_does_not_show_a_problem_it_could_not_check():
    led = ready_led()

    def broken():
        raise FileNotFoundError("nmcli")
    assert NetworkMonitor(led, probe=broken).check_once() == NET_OK and led.mode == "IDLE"


# ------------------------------------------------------------------ driven by the main loop

def test_boot_shows_setup_when_no_wifi_is_configured(monkeypatch):
    """RF41: once the service is up, an unconfigured machine shows solid blue (pair me), never green first;
    after the Bluetooth setup connects the Wi-Fi it turns green."""
    import sorter_api.api as api_module
    state = {"net": NET_SETUP}

    class FakeMonitor(NetworkMonitor):
        def __init__(self, led):
            super().__init__(led, interval_s=0.02, probe=lambda: state["net"])
    monkeypatch.setattr(api_module, "NetworkMonitor", FakeMonitor)
    with TestClient(create_app(fast_settings(network_monitor="on"), vision=ScriptedVision([]))) as c:
        led = c.app.state.status_led
        assert led.history == ["STARTING", "SETUP"]
        state["net"] = NET_OK                          # the app sent the Wi-Fi over Bluetooth
        deadline = time.monotonic() + 2
        while led.mode != "IDLE" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert led.history == ["STARTING", "SETUP", "IDLE"]


def test_starting_lasts_until_the_power_on_homing_is_over():
    """Appendix B Idle = "power-on, homing complete": white while the base homes, then green."""
    with TestClient(create_app(fast_settings(home_on_power_on=True, sim_time_scale=0.3),
                               vision=ScriptedVision([]))) as c:
        led = c.app.state.status_led
        assert c.app.state.controller.homing_at_power_on and led.mode == "STARTING"
        deadline = time.monotonic() + 20
        while c.app.state.controller.homing_at_power_on and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.05)
        assert led.mode == "IDLE" and led.history == ["STARTING", "IDLE"]


def test_service_stop_turns_the_led_off():
    with TestClient(create_app(fast_settings(), vision=ScriptedVision([]))) as c:
        driver = c.app.state.status_led.driver
        assert c.app.state.status_led.mode == "IDLE"
    assert driver.color == (0, 0, 0)


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
