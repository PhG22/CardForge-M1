from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from ..config import CameraConfig

log = logging.getLogger(__name__)

# Limites usados quando a câmera não informa os seus (backend rpicam, fotos): (mín, máx).
SENSOR_LIMITS = {
    "imx219": {"exposure_us": (100, 1_000_000), "analogue_gain": (1.0, 10.666)},   # Camera Module v2
}
DEFAULT_LIMITS = {"exposure_us": (100, 1_000_000), "analogue_gain": (1.0, 16.0)}


@dataclass
class Frame:
    image: np.ndarray                                   # H × W × 3, BGR
    captured_at: datetime = field(default_factory=datetime.now)
    source: str = "camera"


def sharpness(image: np.ndarray) -> float:
    """Variância do Laplaciano — métrica de foco usada no diagnóstico (RF15)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class _Tunable:
    """
    Ajustes de exposição usados pela calibração remota (página /calibration do serviço).
    Os valores ficam em self.cfg; quem quiser persisti-los grava o vision.toml.
    """

    cfg: CameraConfig
    backend = "?"
    detected_sensor: Optional[str] = None       # modelo informado pela câmera (picamera2)
    _detected_limits: Optional[dict] = None

    def limits(self) -> dict:
        return self._detected_limits or SENSOR_LIMITS.get(self.cfg.sensor, DEFAULT_LIMITS)

    def settings(self) -> dict:
        c = self.cfg
        return {"backend": self.backend, "sensor": self.detected_sensor or c.sensor,
                "resolution": list(c.resolution), "exposure_us": int(c.exposure_us),
                "analogue_gain": round(float(c.analogue_gain), 3),
                "awb_gains": [round(float(g), 3) for g in c.awb_gains],
                "limits": {k: list(v) for k, v in self.limits().items()}}

    def apply(self, exposure_us: Optional[int] = None, analogue_gain: Optional[float] = None,
              awb_gains: Optional[tuple[float, float]] = None) -> dict:
        lim = self.limits()
        if exposure_us is not None:
            self.cfg.exposure_us = int(min(max(exposure_us, lim["exposure_us"][0]), lim["exposure_us"][1]))
        if analogue_gain is not None:
            self.cfg.analogue_gain = float(min(max(analogue_gain, lim["analogue_gain"][0]),
                                               lim["analogue_gain"][1]))
        if awb_gains is not None:
            self.cfg.awb_gains = tuple(float(min(max(g, 0.0), 8.0)) for g in awb_gains)
        self._apply_controls()
        return self.settings()

    def _apply_controls(self) -> None:
        pass


class CameraService(_Tunable):
    """
    Pi Camera Module v2 (IMX219) via Picamera2 — a biblioteca Python oficial da Raspberry Pi, sobre a
    pilha libcamera (a mesma das ferramentas rpicam-*). O sensor é detectado sozinho e o libcamera usa o
    arquivo de ajuste dele (imx219.json). Exposição e balanço de branco fixos para que o brilho dependa
    só das fitas LED da câmara (RNF08) e dos degraus de luz do controle (RNF09).
    """

    backend = "picamera2"

    def __init__(self, cfg: CameraConfig | None = None):
        self.cfg = cfg or CameraConfig()
        self.camera = None

    def configure(self) -> None:
        from picamera2 import Picamera2  # só existe no Raspberry Pi (apt: python3-picamera2)

        cam = Picamera2()
        self.detected_sensor = str(cam.camera_properties.get("Model", "")) or None
        if self.detected_sensor and self.cfg.sensor and self.detected_sensor.lower() != self.cfg.sensor.lower():
            log.warning("vision.toml expects sensor %s but the camera reports %s: check the camera cable and "
                        "camera_auto_detect / dtoverlay in /boot/firmware/config.txt",
                        self.cfg.sensor, self.detected_sensor)
        controls = cam.camera_controls        # nome -> (mín, máx, padrão), informado pelo sensor
        if "ExposureTime" in controls and "AnalogueGain" in controls:
            exp, gain = controls["ExposureTime"], controls["AnalogueGain"]
            self._detected_limits = {"exposure_us": (max(int(exp[0]), 1), min(int(exp[1]), 1_000_000)),
                                     "analogue_gain": (float(gain[0]), float(gain[1]))}
        # "RGB888" no libcamera entrega os bytes na ordem B, G, R — já é o formato do OpenCV.
        cam.configure(cam.create_still_configuration(main={"size": tuple(self.cfg.resolution), "format": "RGB888"}))
        self.camera = cam
        self._apply_controls()
        cam.start()
        time.sleep(self.cfg.warmup_s)

    def _controls(self) -> dict:
        controls = {"AeEnable": False, "ExposureTime": int(self.cfg.exposure_us),
                    "AnalogueGain": float(self.cfg.analogue_gain)}
        if self.cfg.awb_gains and all(g > 0 for g in self.cfg.awb_gains):
            controls.update({"AwbEnable": False, "ColourGains": tuple(self.cfg.awb_gains)})
        else:
            controls["AwbEnable"] = True
        return controls

    def _apply_controls(self) -> None:
        if self.camera is not None:
            self.camera.set_controls(self._controls())

    def capture(self) -> Frame:
        if self.camera is None:
            self.configure()
        return Frame(self.camera.capture_array("main"))

    def auto_calibrate(self, settle_s: float = 2.5) -> dict:
        """
        Liga exposição e balanço de branco automáticos com a carta na câmara, espera convergirem,
        lê os valores escolhidos pela câmara e os TRAVA (passam a ser fixos, como numa run).
        """
        if self.camera is None:
            self.configure()
        self.camera.set_controls({"AeEnable": True, "AwbEnable": True})
        time.sleep(settle_s)
        md = self.camera.capture_metadata()
        self.cfg.exposure_us = int(md["ExposureTime"])
        self.cfg.analogue_gain = float(md["AnalogueGain"])
        self.cfg.awb_gains = tuple(float(g) for g in md["ColourGains"])
        self._apply_controls()
        time.sleep(0.3)                     # os controles valem a partir dos próximos quadros
        return self.settings()

    def close(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera.close()
            self.camera = None


class RpicamCameraService(_Tunable):
    """
    Alternativa sem Picamera2: chama `rpicam-still` (rpicam-apps, Raspberry Pi OS Bookworm ou mais novo)
    a cada captura. Mais lento (~1 s por foto, a câmera é aberta a cada vez), útil se o picamera2 não
    estiver disponível no venv. Seleção: [camera] backend = "rpicam" no vision.toml.
    """

    backend = "rpicam"
    COMMAND = "rpicam-still"

    def __init__(self, cfg: CameraConfig | None = None, runner=subprocess.run):
        self.cfg = cfg or CameraConfig()
        self._run = runner

    def configure(self) -> None:
        if self._run is subprocess.run and shutil.which(self.COMMAND) is None:
            raise RuntimeError(f"{self.COMMAND} not found: sudo apt install rpicam-apps")

    def command(self, output: str, auto: bool = False) -> list[str]:
        w, h = self.cfg.resolution
        cmd = [self.COMMAND, "-n", "--width", str(w), "--height", str(h), "-e", "png", "-o", output]
        if auto:                            # deixa AE/AWB convergirem e devolve os valores escolhidos
            return cmd + ["-t", "2000", "--metadata", "-", "--metadata-format", "json"]
        cmd += ["--immediate", "--shutter", str(int(self.cfg.exposure_us)),
                "--gain", f"{self.cfg.analogue_gain:.3f}"]
        if self.cfg.awb_gains and all(g > 0 for g in self.cfg.awb_gains):
            cmd += ["--awbgains", f"{self.cfg.awb_gains[0]:.3f},{self.cfg.awb_gains[1]:.3f}"]
        return cmd

    def capture(self) -> Frame:
        proc = self._run(self.command("-"), capture_output=True, timeout=15, check=True)
        image = cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"{self.COMMAND} returned no image")
        return Frame(image, source=self.COMMAND)

    def auto_calibrate(self, settle_s: float = 2.0) -> dict:
        Path(self.cfg.tmp_dir).mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".png", dir=self.cfg.tmp_dir)
        os.close(fd)
        try:
            proc = self._run(self.command(tmp, auto=True), capture_output=True, timeout=20, check=True)
            md = json.loads(proc.stdout.decode("utf-8", "replace"))
        finally:
            Path(tmp).unlink(missing_ok=True)
        self.cfg.exposure_us = int(md["ExposureTime"])
        self.cfg.analogue_gain = float(md["AnalogueGain"])
        self.cfg.awb_gains = tuple(float(g) for g in md["ColourGains"])
        return self.settings()

    def close(self) -> None:
        pass


class FileCameraService(_Tunable):
    """
    Substituto da câmara para testes/bancada: devolve imagens de disco, em ciclo. Simula os ajustes
    (exposição × ganho escalam o brilho; ganhos de branco escalam vermelho/azul) para que a página de
    calibração possa ser testada sem o Raspberry Pi.
    """

    backend = "files"

    def __init__(self, paths: list[str | Path], cfg: CameraConfig | None = None):
        if not paths:
            raise ValueError("FileCameraService needs at least one image")
        self.paths = [Path(p) for p in paths]
        self._i = 0
        self.cfg = cfg or CameraConfig()
        d = CameraConfig()
        self._reference = (d.exposure_us * d.analogue_gain, d.awb_gains)
        self.hold = False                   # True = repete a última imagem (carta parada na câmara)

    def configure(self) -> None:
        pass

    def advance(self) -> None:
        """A nova carta chegou à plataforma: a próxima captura mostra a próxima foto."""
        if self._i > 0:                     # a foto atual já foi mostrada
            self._i += 1

    def _next_path(self) -> Path:
        if self.hold and self._i > 0:
            return self.paths[(self._i - 1) % len(self.paths)]
        path = self.paths[self._i % len(self.paths)]
        self._i += 1
        return path

    def capture(self) -> Frame:
        path = self._next_path()
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"cannot read image: {path}")
        light, (r0, b0) = self._reference
        scale = (self.cfg.exposure_us * self.cfg.analogue_gain) / light
        r, b = self.cfg.awb_gains if all(g > 0 for g in self.cfg.awb_gains) else (r0, b0)
        if abs(scale - 1) > 1e-6 or (r, b) != (r0, b0):
            gains = np.array([b / b0, 1.0, r / r0], np.float32) * scale
            image = np.clip(image.astype(np.float32) * gains, 0, 255).astype(np.uint8)
        return Frame(image, source=str(path))

    def auto_calibrate(self, settle_s: float = 0.0) -> dict:
        """Simulação: escolhe a exposição que deixa o brilho médio em ~45 % e o branco neutro."""
        self.cfg.awb_gains = self._reference[1]
        self.cfg.exposure_us, self.cfg.analogue_gain = int(self._reference[0] / 2.0), 2.0
        mean = float(cv2.cvtColor(self.capture().image, cv2.COLOR_BGR2GRAY).mean())
        self.cfg.exposure_us = int(self.cfg.exposure_us * min(max(115.0 / max(mean, 1.0), 0.1), 10.0))
        return self.settings()

    def close(self) -> None:
        pass


def make_camera(cfg: CameraConfig):
    """Câmara real conforme [camera] backend do vision.toml."""
    if cfg.backend == "picamera2":
        return CameraService(cfg)
    if cfg.backend == "rpicam":
        return RpicamCameraService(cfg)
    raise ValueError(f"[camera] backend must be picamera2 or rpicam (got {cfg.backend!r})")
