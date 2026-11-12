from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..config import VisionConfig
from .detector import CardQuad
from .geometry import four_point_transform, order_points  # noqa: F401 (reexportados)


@dataclass
class RectifiedCard:
    image: np.ndarray            # 880 × 630 × 3 (BGR), carta em pé
    rotated180: bool = False

    def rotated(self) -> "RectifiedCard":
        return RectifiedCard(cv2.rotate(self.image, cv2.ROTATE_180), not self.rotated180)


class PerspectiveRectifier:
    def __init__(self, cfg: VisionConfig):
        self.cfg = cfg

    def rectify(self, image: np.ndarray, quad: CardQuad) -> RectifiedCard:
        if quad.strategy.value == "FIXED":           # cantos já na ordem da carta: sai em pé, sem girar
            return self.rectify_ordered(image, quad.corners)
        warped = four_point_transform(image, quad.corners)
        if warped.shape[1] > warped.shape[0]:  # paisagem -> em pé
            warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
        return RectifiedCard(cv2.resize(warped, self.cfg.card_size, interpolation=cv2.INTER_AREA))

    def rectify_ordered(self, image: np.ndarray, corners: np.ndarray) -> RectifiedCard:
        """Cantos na ordem da carta (topo-esquerda da carta primeiro) -> carta em pé, 630 x 880."""
        w, h = self.cfg.card_size
        dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        m = cv2.getPerspectiveTransform(np.asarray(corners, dtype=np.float32).reshape(4, 2), dst)
        return RectifiedCard(cv2.warpPerspective(image, m, (w, h), flags=cv2.INTER_AREA))
