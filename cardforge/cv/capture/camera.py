from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from ..config import CameraConfig


@dataclass
class Frame:
    image: np.ndarray                                   # H × W × 3, BGR
    captured_at: datetime = field(default_factory=datetime.now)
    source: str = "camera"


def sharpness(image: np.ndarray) -> float:
    """Variância do Laplaciano — métrica de foco usada no diagnóstico (RF15)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class CameraService:
    """
    Câmara OV5647 via Picamera2 (libcamera, CSI-2). Exposição e balanço de branco fixos para que
    o brilho dependa só das fitas LED da câmara (RNF08) e dos degraus de luz do controle (RNF09).
    """

    def __init__(self, cfg: CameraConfig | None = None):
        self.cfg = cfg or CameraConfig()
        self.camera = None

    def configure(self) -> None:
        from picamera2 import Picamera2  # só existe no Raspberry Pi

        cam = Picamera2()
        # "RGB888" no libcamera entrega os bytes na ordem B, G, R — já é o formato do OpenCV.
        cam.configure(cam.create_still_configuration(main={"size": tuple(self.cfg.resolution), "format": "RGB888"}))
        controls = {"AeEnable": False, "ExposureTime": int(self.cfg.exposure_us),
                    "AnalogueGain": float(self.cfg.analogue_gain)}
        if self.cfg.awb_gains and all(g > 0 for g in self.cfg.awb_gains):
            controls.update({"AwbEnable": False, "ColourGains": tuple(self.cfg.awb_gains)})
        cam.set_controls(controls)
        cam.start()
        time.sleep(self.cfg.warmup_s)
        self.camera = cam

    def capture(self) -> Frame:
        if self.camera is None:
            self.configure()
        return Frame(self.camera.capture_array("main"))

    def close(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera.close()
            self.camera = None


class FileCameraService:
    """Substituto da câmara para testes/bancada: devolve imagens de disco, em ciclo."""

    def __init__(self, paths: list[str | Path]):
        if not paths:
            raise ValueError("FileCameraService needs at least one image")
        self.paths = [Path(p) for p in paths]
        self._i = 0

    def configure(self) -> None:
        pass

    def capture(self) -> Frame:
        path = self.paths[self._i % len(self.paths)]
        self._i += 1
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"cannot read image: {path}")
        return Frame(image, source=str(path))

    def close(self) -> None:
        pass
