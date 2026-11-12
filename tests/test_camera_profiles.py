"""Two camera modules (IMX219 = Camera Module v2, OV5647 = v1): detected at start, each with its own profile and crop."""

import subprocess
import sys
import types

import numpy as np

from cardforge.cv.config import CameraConfig, VisionConfig
from cardforge.cv.capture import CameraService, RpicamCameraService


class FakeCamera:
    model = "imx219"

    def __init__(self):
        self.camera_properties = {"Model": FakeCamera.model}
        self.camera_controls = {"ExposureTime": (75, 11_766_829, 20000), "AnalogueGain": (1.0, 10.666667, 1.0)}
        self.config, self.controls = None, []

    def create_still_configuration(self, main):
        return {"main": main}

    def configure(self, config):
        self.config = config

    def set_controls(self, controls):
        self.controls.append(dict(controls))

    def start(self):
        pass

    def capture_array(self, name):
        return np.zeros((8, 8, 3), np.uint8)


def plug(monkeypatch, model):
    FakeCamera.model = model
    monkeypatch.setitem(sys.modules, "picamera2", types.SimpleNamespace(Picamera2=FakeCamera))


def test_each_module_gets_its_own_resolution(monkeypatch):
    for model, size in (("imx219", (3280, 2464)), ("ov5647", (2592, 1944))):
        plug(monkeypatch, model)
        cam = CameraService(CameraConfig(warmup_s=0))
        cam.configure()
        assert cam.sensor == model and cam.camera.config["main"]["size"] == size


def test_profile_values_from_vision_toml_win(monkeypatch):
    plug(monkeypatch, "ov5647")
    cfg = CameraConfig(warmup_s=0, profiles={"ov5647": {"exposure_us": 7000, "awb_gains": [2.0, 1.2]},
                                             "imx219": {"exposure_us": 9000}})
    cam = CameraService(cfg)
    cam.configure()
    assert cam.camera.controls[0]["ExposureTime"] == 7000 and cam.camera.controls[0]["ColourGains"] == (2.0, 1.2)
    assert cfg.resolution == (2592, 1944)


def test_rpicam_lists_the_connected_camera():
    def runner(cmd, **kw):
        out = b"Available cameras\n-----------------\n0 : ov5647 [2592x1944 10-bit GBRG] (/base/soc/i2c0mux)\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=b"")
    cam = RpicamCameraService(CameraConfig(), runner)
    cam.configure()
    assert cam.sensor == "ov5647" and cam.cfg.resolution == (2592, 1944)
    assert cam.command("-")[cam.command("-").index("--width") + 1] == "2592"


class Vision:
    """VisionModule with a fake camera, to test what is saved per sensor."""

    def __init__(self, tmp_path, model):
        from cardforge.cv import VisionModule

        toml = tmp_path / "vision.toml"
        toml.write_text('[camera]\nsensor = "auto"\n\n[crop]\nmode = "fixed"\ncorners = []\n\n'
                        '[crop.corners_by_sensor]\nimx219 = [[0.8, 0.3], [0.85, 0.95], [0.16, 0.97], [0.15, 0.31]]\n',
                        encoding="utf-8")
        self.toml = toml
        cfg = VisionConfig.load(toml)
        self.v = VisionModule(cfg, camera=types.SimpleNamespace(sensor=model, configure=lambda: None,
                                                                close=lambda: None))
        self.v.camera.settings = lambda: {}
        self.v._use_sensor_crop()


def test_the_crop_follows_the_connected_camera(tmp_path):
    assert len(Vision(tmp_path, "imx219").v.cfg.crop.corners) == 4
    assert Vision(tmp_path, "ov5647").v.cfg.crop.corners == []          # not calibrated for this module yet


def test_calibration_is_saved_in_the_camera_profile(tmp_path):
    w = Vision(tmp_path, "ov5647")
    w.v.cfg.camera.exposure_us = 12345
    w.v.save_camera_settings()
    w.v.save_crop([[0.7, 0.2], [0.75, 0.9], [0.2, 0.92], [0.18, 0.22]])
    cfg = VisionConfig.load(w.toml)
    assert cfg.camera.profiles["ov5647"]["exposure_us"] == 12345 and "imx219" not in cfg.camera.profiles
    assert cfg.crop.corners_by_sensor["ov5647"][0] == [0.7, 0.2]
    assert cfg.crop.corners_by_sensor["imx219"][0] == [0.8, 0.3]        # the other module keeps its crop
    assert cfg.crop.corners == []
