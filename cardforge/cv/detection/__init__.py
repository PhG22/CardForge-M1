"""Pacote cardforge.cv.detection — borda da carta e correção de perspectiva (CV-SD-02a, CV-AD-02)."""

from .detector import CardDetector, CardQuad, ContourCandidateGenerator, ContourStrategy, QuadrilateralFinder, auto_canny
from .rectifier import PerspectiveRectifier, RectifiedCard, four_point_transform, order_points

__all__ = [
    "CardDetector", "CardQuad", "ContourCandidateGenerator", "ContourStrategy", "QuadrilateralFinder",
    "auto_canny", "PerspectiveRectifier", "RectifiedCard", "four_point_transform", "order_points",
]
