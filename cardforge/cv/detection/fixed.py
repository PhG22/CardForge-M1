from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from ..config import CropConfig
from .detector import CardQuad, ContourStrategy
from .geometry import order_points


def fixed_quad(crop: CropConfig, frame_shape: tuple[int, int]) -> Optional[CardQuad]:
    """Quadrilátero do recorte fixo na resolução do quadro, ou None se não foi calibrado."""
    if len(crop.corners) != 4:
        return None
    h, w = frame_shape[:2]
    pts = np.array(crop.corners, dtype=np.float32).reshape(4, 2) * np.array([w, h], dtype=np.float32)
    if crop.margin:
        center = pts.mean(axis=0)
        pts = center + (pts - center) * (1.0 + float(crop.margin))
    pts = order_points(pts)
    return CardQuad(pts, float(cv2.contourArea(pts)), ContourStrategy.FIXED)


def normalized_corners(quad: CardQuad, frame_shape: tuple[int, int]) -> list[list[float]]:
    """Cantos (TL, TR, BR, BL) em fração do quadro, como ficam gravados no vision.toml."""
    h, w = frame_shape[:2]
    return [[round(float(x) / w, 5), round(float(y) / h, 5)] for x, y in order_points(quad.corners)]
