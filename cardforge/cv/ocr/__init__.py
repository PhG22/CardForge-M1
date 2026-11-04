"""Pacote cardforge.cv.ocr — orientação, verso da carta, layout e leitura dos campos (CV-CL-02)."""

from .engine import OcrEngine, OcrLine
from .facedown import FaceDownDetector
from .fields import ExtractedFields, FieldExtractor
from .layout import LayoutClassifier
from .orientation import OrientationResolver, OrientationResult
from .rois import CardLayout, Roi, RoiCatalog, RoiSet

__all__ = [
    "OcrEngine", "OcrLine", "FaceDownDetector", "ExtractedFields", "FieldExtractor", "LayoutClassifier",
    "OrientationResolver", "OrientationResult", "CardLayout", "Roi", "RoiCatalog", "RoiSet",
]
