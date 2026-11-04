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
        warped = four_point_transform(image, quad.corners)
        if warped.shape[1] > warped.shape[0]:  # paisagem -> em pé
            warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
        return RectifiedCard(cv2.resize(warped, self.cfg.card_size, interpolation=cv2.INTER_AREA))
