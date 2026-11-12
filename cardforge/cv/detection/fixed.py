from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from ..config import CropConfig
from .detector import CardQuad, ContourStrategy
from .geometry import order_points


def fixed_quad(crop: CropConfig, frame_shape: tuple[int, int]) -> Optional[CardQuad]:
    """Quadrilátero do recorte fixo na resolução do quadro, na ordem da carta, ou None se não foi calibrado."""
    if len(crop.corners) != 4:
        return None
    h, w = frame_shape[:2]
    pts = np.array(crop.corners, dtype=np.float32).reshape(4, 2) * np.array([w, h], dtype=np.float32)
    if crop.margin:
        center = pts.mean(axis=0)
        pts = center + (pts - center) * (1.0 + float(crop.margin))
    return CardQuad(pts.astype(np.float32), float(cv2.contourArea(pts)), ContourStrategy.FIXED)


def to_card_order(pts: np.ndarray) -> np.ndarray:
    """
    Cantos na ordem da imagem (TL, TR, BR, BL) -> ordem da carta: o primeiro lado (canto 0 -> 1) passa a ser
    o lado CURTO, que é o topo da carta. Uma carta deitada na imagem (como na câmara de leitura, com o título
    na borda direita) começa então pelo canto superior direito da imagem. Os 180° que sobram de ambiguidade
    são resolvidos pela orientação (OCR) ou pelo botão "Girar 180°" da calibração.
    """
    p = order_points(pts)
    if np.linalg.norm(p[1] - p[0]) > np.linalg.norm(p[2] - p[1]):
        p = p[[1, 2, 3, 0]]
    return p


def normalized_corners(quad: CardQuad, frame_shape: tuple[int, int]) -> list[list[float]]:
    """Cantos em fração do quadro e na ordem da carta, como ficam gravados no vision.toml."""
    h, w = frame_shape[:2]
    return [[round(float(x) / w, 5), round(float(y) / h, 5)] for x, y in to_card_order(quad.corners)]
