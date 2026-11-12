from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

from ..data.repository import DatabaseUnavailable, SqliteCardRepository
from ..data.sync import ScryfallSyncService, SyncReport
from .capture.camera import Frame, make_camera
from .config import VisionConfig
from .matching import NameIndex, SetCodeCatalog
from .ocr import OcrEngine
from .recognizer import CardRecognizer
from .result import IdentificationResult

log = logging.getLogger(__name__)


class VisionModule:
    """
    Fachada do subsistema de visão usada pelo programa de controle (MainLoop, FW-CL-01).

        vision = VisionModule(VisionConfig.load("vision.toml"))
        vision.start()                 # carrega modelos ONNX + cards.db; DatabaseUnavailable => pre-run block
        result = vision.identify()     # 1 captura + 1 reconhecimento; retentativas/luz são do controle
        vision.update_database()       # só com nenhuma run ativa

    Apenas uma identificação ou atualização acontece de cada vez (lock interno).
    """

    def __init__(self, cfg: Optional[VisionConfig] = None, camera=None):
        self.cfg = cfg or VisionConfig.load()
        self.camera = camera if camera is not None else make_camera(self.cfg.camera)
        self.ocr: Optional[OcrEngine] = None
        self.repo: Optional[SqliteCardRepository] = None
        self.names = NameIndex()
        self.sets = SetCodeCatalog()
        self.recognizer: Optional[CardRecognizer] = None
        self._lock = threading.RLock()

    @property
    def ready(self) -> bool:
        return self.recognizer is not None

    def start(self, open_camera: bool = True) -> None:
        """Arranque do serviço (CV-SD-04): modelos de OCR, banco local, índices em memória e câmara."""
        with self._lock:
            self._open_database()
            if open_camera:
                self.camera.configure()
                self._use_sensor_crop()

    @property
    def sensor(self):
        """Sensor da câmera (imx219, ov5647...) ou None se desconhecido (ex.: fotos de arquivo)."""
        return getattr(self.camera, "sensor", None)

    def _use_sensor_crop(self) -> None:
        """Cada câmera enquadra a carta de um jeito: usa o recorte calibrado para o sensor conectado."""
        corners = self.cfg.crop.corners_by_sensor.get(self.sensor or "")
        if corners:
            self.cfg.crop.corners = [list(map(float, c)) for c in corners]
        elif self.sensor and self.cfg.crop.mode == "fixed" and self.cfg.crop.corners_by_sensor:
            log.warning("no fixed crop calibrated for the %s camera yet", self.sensor)
            self.cfg.crop.corners = []

    def _open_database(self) -> None:
        if self.ocr is None:
            self.ocr = OcrEngine(self.cfg.ocr_det_limit_type, self.cfg.ocr_det_limit_side_len, self.cfg.ocr_angle_cls)
        if self.repo is None:
            self.repo = SqliteCardRepository(self.cfg.db_path)  # pode lançar DatabaseUnavailable
        else:
            self.repo.reopen()
        self.names.build(self.repo.all_name_keys())
        self.sets.load(self.repo.all_set_codes())
        self.recognizer = CardRecognizer(self.cfg, self.ocr, self.repo, self.names, self.sets)
        log.info("vision ready: %d names, %d set codes", len(self.names), len(self.sets.codes))

    def identify(self) -> IdentificationResult:
        """Uma captura e um reconhecimento (CV-SD-01). O cartão deve estar parado pelo gate (RF02)."""
        with self._lock:
            if not self.ready:
                raise DatabaseUnavailable("vision module not started")
            t0 = time.perf_counter()
            frame = self.camera.capture()
            capture_ms = int((time.perf_counter() - t0) * 1000)
            result = self.recognizer.recognize(frame)
            result.debug["capture_ms"] = capture_ms          # RF02 / RF15: the photo itself
            return result

    def recognize(self, frame: Frame | np.ndarray) -> IdentificationResult:
        """Reconhece uma imagem já capturada (bancada, testes, relatórios)."""
        with self._lock:
            if not self.ready:
                raise DatabaseUnavailable("vision module not started")
            return self.recognizer.recognize(frame)

    def reload_database(self) -> None:
        with self._lock:
            self._open_database()

    def update_database(self, force: bool = False) -> SyncReport:
        """
        Atualiza o espelho do Scryfall (CV-SD-04). Deve ser chamado só sem run ativa;
        se falhar, o cards.db anterior continua em uso.
        """
        with self._lock:
            service = ScryfallSyncService(self.cfg.db_path)

            def close_repo() -> None:
                if self.repo is not None:
                    self.repo.close()

            try:
                report = service.sync(force=force, before_replace=close_repo)
            except Exception:
                if self.repo is not None and self.repo.conn is None:
                    self._open_database()  # a troca não aconteceu: volta ao banco anterior
                raise
            if report.updated or self.repo is None:
                self._open_database()
            return report

    # ------------------------------------------------ calibração com a máquina montada

    def camera_settings(self) -> dict:
        with self._lock:
            return {**self.camera.settings(), "light_level": self.cfg.camera.light_level}

    def tune_camera(self, exposure_us=None, analogue_gain=None, awb_gains=None) -> dict:
        """Ajuste ao vivo (vale para as próximas capturas; save_camera_settings() grava)."""
        with self._lock:
            self.camera.apply(exposure_us, analogue_gain, awb_gains)
            return self.camera_settings()

    def auto_tune_camera(self) -> dict:
        """Exposição e branco automáticos com a carta na câmara, depois travados."""
        with self._lock:
            self.camera.auto_calibrate()
            return self.camera_settings()

    def calibration_snapshot(self):
        """Uma captura + métricas (brilho, carta, uniformidade, foco) + imagem anotada."""
        from .calibration import frame_metrics

        with self._lock:
            frame = self.camera.capture()
        metrics, overlay = frame_metrics(frame.image, self.cfg)
        return frame, metrics, overlay

    def measure_crop(self):
        """Onde está a carta parada na plataforma, pela detecção de bordas (só para calibrar o recorte fixo).
        Devolve (cantos normalizados TL, TR, BR, BL ou None, a foto, o quadrilátero em pixels ou None)."""
        from .detection import CardDetector, normalized_corners

        with self._lock:
            frame = self.camera.capture()
        quad = CardDetector(self.cfg).detect(frame.image)
        corners = normalized_corners(quad, frame.image.shape) if quad is not None else None
        return corners, frame.image, quad

    def save_crop(self, corners: list, path=None) -> str:
        """Liga o recorte fixo com estes cantos (vale na hora) e grava a seção [crop] do vision.toml."""
        from .calibration import save_section

        if len(corners) != 4:
            raise ValueError("the fixed crop needs 4 corners")
        path = path or getattr(self.cfg, "source_path", None) or "vision.toml"
        with self._lock:
            self.cfg.crop.mode = "fixed"
            self.cfg.crop.corners = [[round(float(x), 5), round(float(y), 5)] for x, y in corners]
            if self.sensor:                          # um recorte por câmera
                self.cfg.crop.corners_by_sensor[self.sensor] = self.cfg.crop.corners
                save_section(path, "crop", {"mode": "fixed", "margin": float(self.cfg.crop.margin)})
                save_section(path, "crop.corners_by_sensor", {self.sensor: self.cfg.crop.corners})
            else:
                save_section(path, "crop", {"mode": "fixed", "corners": self.cfg.crop.corners,
                                            "margin": float(self.cfg.crop.margin)})
        return str(path)

    def save_camera_settings(self, path=None) -> str:
        """Grava exposição, ganho e branco no perfil do sensor ([camera.profiles.<sensor>]) e a luz em [camera]."""
        from .calibration import save_section

        path = path or getattr(self.cfg, "source_path", None) or "vision.toml"
        with self._lock:
            c = self.cfg.camera
            values = {"exposure_us": int(c.exposure_us), "analogue_gain": round(float(c.analogue_gain), 3),
                      "awb_gains": [round(float(g), 3) for g in c.awb_gains]}
            if self.sensor:
                c.profiles.setdefault(self.sensor, {}).update(values)
                save_section(path, f"camera.profiles.{self.sensor}", values)
                save_section(path, "camera", {"light_level": int(c.light_level)})
            else:
                save_section(path, "camera", {**values, "light_level": int(c.light_level)})
        return str(path)

    def close(self) -> None:
        with self._lock:
            self.camera.close()
            if self.repo is not None:
                self.repo.close()
