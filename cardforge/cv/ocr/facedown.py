from __future__ import annotations

import cv2
import numpy as np

from ..config import FaceDownConfig
from .orientation import OrientationResult


class FaceDownDetector:
    """
    Carta virada para baixo (RF39): nenhum título lido em nenhuma orientação E a assinatura de cor
    do verso de Magic — moldura e oval central marrom-alaranjados cobrindo quase toda a carta.
    Medido: verso ≈ 0,85 de pixels marrons; frentes ≤ 0,67 (pior caso: carta vermelha foil).
    """

    def __init__(self, cfg: FaceDownConfig):
        self.cfg = cfg

    @staticmethod
    def brown_ratio(img: np.ndarray) -> float:
        """Fração de pixels marrom/laranja no interior da carta (sem a borda preta de ~5 %)."""
        h, w = img.shape[:2]
        hsv = cv2.cvtColor(img[int(.05 * h):int(.95 * h), int(.05 * w):int(.95 * w)], cv2.COLOR_BGR2HSV)
        # Matiz laranja no OpenCV (0–179), saturação média/alta, brilho baixo a médio-alto.
        return float((cv2.inRange(hsv, (3, 60, 25), (28, 255, 235)) > 0).mean())

    def matches_card_back(self, img: np.ndarray) -> bool:
        return self.brown_ratio(img) >= self.cfg.min_brown_ratio

    def is_face_down(self, o: OrientationResult) -> bool:
        # Sem rotação tentada = o título foi lido na orientação normal.
        no_title = (o.tried_rotation and o.title_chars <= self.cfg.max_text_chars
                    and o.rotated_title_chars <= self.cfg.max_text_chars)
        return no_title and self.matches_card_back(o.card.image)
