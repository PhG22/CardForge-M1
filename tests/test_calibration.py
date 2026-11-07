"""Camera calibration with the machine assembled: vision.toml writer, rpicam backend, /calibration routes."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cardforge.cv.calibration import save_section
from cardforge.cv.capture import RpicamCameraService
from cardforge.cv.config import CameraConfig, VisionConfig
from sorter_api.api import create_app
from test_integration import fast_settings

ROOT = Path(__file__).resolve().parents[1]


def test_save_section_keeps_comments_and_other_sections(tmp_path):
    p = tmp_path / "vision.toml"
    p.write_text('db_path = "data/cards.db"\n\n[camera]   # câmera\nexposure_us = 20000    # fixa\n'
                 'analogue_gain = 2.0\n\n[face_down]\nmin_brown_ratio = 0.75\n', encoding="utf-8")
    save_section(p, "camera", {"exposure_us": 12345, "awb_gains": [1.81, 1.42], "light_level": 70})
    text = p.read_text(encoding="utf-8")
    assert "exposure_us = 12345  # fixa" in text and "[face_down]\nmin_brown_ratio = 0.75" in text
    cfg = VisionConfig.load(p)
    assert (cfg.camera.exposure_us, cfg.camera.awb_gains, cfg.camera.light_level) == (12345, (1.81, 1.42), 70)
    assert cfg.camera.analogue_gain == 2.0 and cfg.face_down.min_brown_ratio == 0.75


def test_save_section_creates_a_missing_section(tmp_path):
    p = tmp_path / "vision.toml"
    p.write_text("confidence_threshold = 90\n", encoding="utf-8")
    save_section(p, "camera", {"exposure_us": 900})
    assert VisionConfig.load(p).camera.exposure_us == 900


def test_rpicam_backend_builds_the_rpicam_still_command():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if "--metadata" in cmd:
            out = json.dumps({"ExposureTime": 8123, "AnalogueGain": 1.75, "ColourGains": [1.9, 1.3]}).encode()
        else:
            import cv2
            import numpy as np
            out = cv2.imencode(".png", np.zeros((4, 4, 3), np.uint8))[1].tobytes()
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=b"")

    cam = RpicamCameraService(CameraConfig(exposure_us=15000, analogue_gain=2.5, awb_gains=(1.6, 1.5)), runner)
    assert cam.capture().image.shape == (4, 4, 3)
    cmd = calls[0]
    assert cmd[0] == "rpicam-still" and "--immediate" in cmd
    assert cmd[cmd.index("--shutter") + 1] == "15000" and cmd[cmd.index("--awbgains") + 1] == "1.600,1.500"
    values = cam.auto_calibrate()
    assert (values["exposure_us"], values["analogue_gain"], values["awb_gains"]) == (8123, 1.75, [1.9, 1.3])
    assert values["sensor"] == "imx219" and values["resolution"] == [1640, 1232]
    assert values["limits"]["analogue_gain"] == [1.0, 10.666]
    assert cam.apply(analogue_gain=16)["analogue_gain"] == 10.666        # clamped to the IMX219 range
    assert "--shutter" not in calls[1] and "--metadata" in calls[1]


def white_target_photo(gradient):
    """A plain white card (dark border) on a gray platform, lit unevenly from the left."""
    import cv2
    import numpy as np
    img = np.full((1520, 2028, 3), 90, np.uint8)
    card = np.full((880, 630, 3), 235, np.float32)
    card *= np.linspace(1 - gradient, 1, 630)[None, :, None]
    card[:25], card[-25:], card[:, :25], card[:, -25:] = 20, 20, 20, 20
    img[320:1200, 700:1330] = card.astype(np.uint8)
    return img


def test_white_target_measures_light_uniformity():
    from cardforge.cv.calibration import frame_metrics
    even, _ = frame_metrics(white_target_photo(0.05), VisionConfig())
    uneven, _ = frame_metrics(white_target_photo(0.45), VisionConfig())
    assert even["card"]["neutral_target"] and uneven["card"]["neutral_target"]
    assert even["card"]["unevenness_pct"] < 20 < uneven["card"]["unevenness_pct"]
    assert any("Luz desigual" in t for t in uneven["advice"])
    assert not any("Luz desigual" in t for t in even["advice"])


# ------------------------------------------------------------------ /calibration routes

needs_vision = pytest.mark.skipif(not (ROOT / "data/cards.db").exists() or not (ROOT / "input_cartas").exists(),
                                  reason="needs data/cards.db and input_cartas")


@pytest.fixture
def calib(tmp_path):
    images = tmp_path / "imgs"
    images.mkdir()
    for i, f in enumerate(["20260925_230815.jpg", "20260925_153311.jpg"]):   # Forest, Chaos Warp
        shutil.copy(ROOT / "input_cartas" / f, images / f"{i}.jpg")
    toml = tmp_path / "vision.toml"
    toml.write_text((ROOT / "vision.toml").read_text(encoding="utf-8").replace(
        'db_path = "data/cards.db"', f'db_path = "{(ROOT / "data/cards.db").as_posix()}"'), encoding="utf-8")
    settings = fast_settings(vision="files", vision_images=str(images), vision_config=str(toml))
    with TestClient(create_app(settings)) as c:
        yield c, toml


@needs_vision
def test_snapshot_measures_the_card(calib):
    c, _ = calib
    assert "Calibração da câmera" in c.get("/calibration").text
    body = c.post("/calibration/snapshot").json()
    m = body["metrics"]
    assert m["card_found"] and 0.3 < m["area_ratio"] < 0.85
    assert len(m["card"]["grid"]) == 9 and m["card"]["info_sharpness"] > 0 and m["advice"]
    assert body["image"].startswith("data:image/jpeg;base64,")
    assert c.get("/calibration/frame.jpg").headers["content-type"] == "image/jpeg"


@needs_vision
def test_settings_change_the_picture_and_are_saved(calib):
    c, toml = calib
    before = c.post("/calibration/snapshot").json()["metrics"]["card"]["brightness"]
    s = c.put("/calibration/settings", json={"exposure_us": 10000, "light_level": 75}).json()["settings"]
    assert s["exposure_us"] == 10000 and s["light_level"] == 75
    after = c.post("/calibration/snapshot").json()["metrics"]["card"]["brightness"]
    assert after < before * 0.7                                  # half the exposure = darker card
    assert "LIGHT 75" in c.app.state.sim_world.top.executed      # chamber light changed live

    auto = c.post("/calibration/auto").json()["settings"]
    assert auto["exposure_us"] != 10000
    assert c.post("/calibration/save").json()["ok"]
    cfg = VisionConfig.load(toml)
    assert (cfg.camera.exposure_us, cfg.camera.light_level) == (auto["exposure_us"], 75)


@needs_vision
def test_reference_reports_the_brightness_change(calib):
    c, _ = calib
    c.post("/calibration/reference")
    c.put("/calibration/settings", json={"analogue_gain": 2.4})   # +20 % light, as if the room lamps were on
    ref = c.post("/calibration/snapshot").json()["metrics"]["reference"]
    assert 15 < ref["delta_pct"] < 25 and ref["limit_pct"] == 10


@needs_vision
def test_feed_identify_and_drop_without_opening_the_machine(calib):
    c, _ = calib
    feed = c.post("/calibration/feed").json()
    assert feed["platform_has_card"] is True and c.get("/sim/state").json()["platform_has_card"]
    ident = c.post("/calibration/identify").json()
    assert ident["result"]["status"] == "IDENTIFIED" and ident["result"]["card"]["name"] == "Forest"
    c.post("/calibration/drop")
    c.post("/calibration/feed")                                   # the next card is the next photo
    assert c.post("/calibration/identify").json()["result"]["card"]["name"] == "Chaos Warp"
    drop = c.post("/calibration/drop").json()
    assert drop["tray"] == 6
    sim = c.get("/sim/state").json()
    assert sim["platform_has_card"] is False and sim["trays_with_cards"] == [6] and sim["flap_angle"] == 80
    assert sim["cards_dropped"] == 2


@needs_vision
def test_calibration_is_refused_during_a_run(calib):
    c, _ = calib
    c.put("/sim/state", json={"stack_cards": 50})
    assert c.post("/run/start").status_code == 200
    r = c.post("/calibration/snapshot")
    assert r.status_code == 409 and r.json()["code"] == "RUN_ACTIVE"
    c.post("/run/stop")


def test_calibration_needs_the_camera():
    with TestClient(create_app(fast_settings(vision="off"))) as c:
        r = c.post("/calibration/snapshot")
        assert r.status_code == 409 and r.json()["code"] == "VISION_OFF"


class FakePicamera2:
    """Just enough of picamera2.Picamera2 for CameraService, reporting an IMX219."""

    model = "imx219"

    def __init__(self):
        self.camera_properties = {"Model": self.model, "PixelArraySize": (3280, 2464)}
        self.camera_controls = {"ExposureTime": (75, 11_766_829, 20000), "AnalogueGain": (1.0, 10.666667, 1.0)}
        self.controls, self.config = [], None

    def create_still_configuration(self, main):
        return {"main": main}

    def configure(self, config):
        self.config = config

    def set_controls(self, controls):
        self.controls.append(dict(controls))

    def start(self):
        pass

    def capture_metadata(self):
        return {"ExposureTime": 12000, "AnalogueGain": 3.0, "ColourGains": (1.95, 1.4)}


def fake_picamera2(monkeypatch, model):
    import sys
    import types
    FakePicamera2.model = model
    monkeypatch.setitem(sys.modules, "picamera2", types.SimpleNamespace(Picamera2=FakePicamera2))


def test_picamera2_uses_the_imx219_limits(monkeypatch, caplog):
    from cardforge.cv.capture import CameraService
    fake_picamera2(monkeypatch, "imx219")
    cam = CameraService(CameraConfig(warmup_s=0))
    cam.configure()
    s = cam.settings()
    assert s["sensor"] == "imx219" and s["resolution"] == [1640, 1232]
    assert cam.camera.config["main"] == {"size": (1640, 1232), "format": "RGB888"}
    assert s["limits"] == {"exposure_us": [75, 1_000_000], "analogue_gain": [1.0, 10.666667]}
    assert cam.camera.controls[0] == {"AeEnable": False, "ExposureTime": 20000, "AnalogueGain": 2.0,
                                      "AwbEnable": False, "ColourGains": (1.6, 1.5)}
    assert cam.apply(analogue_gain=12.0)["analogue_gain"] == 10.667
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert cam.auto_calibrate()["awb_gains"] == [1.95, 1.4]
    assert cam.camera.controls[-1]["ExposureTime"] == 12000 and not cam.camera.controls[-1]["AeEnable"]
    assert "expects sensor" not in caplog.text


def test_picamera2_warns_when_another_camera_is_plugged(monkeypatch, caplog):
    from cardforge.cv.capture import CameraService
    fake_picamera2(monkeypatch, "ov5647")
    CameraService(CameraConfig(warmup_s=0)).configure()
    assert "expects sensor imx219 but the camera reports ov5647" in caplog.text
