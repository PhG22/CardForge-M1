from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from ..data.repository import DatabaseUnavailable, SqliteCardRepository
from ..data.sync import ScryfallSyncService, SyncReport
from .capture.camera import CameraService, Frame
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
        self.camera = camera if camera is not None else CameraService(self.cfg.camera)
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
            frame = self.camera.capture()
            return self.recognizer.recognize(frame)

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

    def close(self) -> None:
        with self._lock:
            self.camera.close()
            if self.repo is not None:
                self.repo.close()
