"""
Pacote cardforge.cv — subsistema de visão computacional do CardForge-M1 (CV-CL-00).

API usada pelo programa de controle:
    VisionModule.start() / identify() -> IdentificationResult / reload_database() / update_database()
"""

from ..data.repository import DatabaseUnavailable
from .config import VisionConfig
from .matching.matcher import MatchMethod
from .ocr.rois import CardLayout
from .recognizer import CardRecognizer
from .result import IdentificationResult, IdentificationStatus
from .vision import VisionModule

__all__ = [
    "VisionModule", "CardRecognizer", "VisionConfig", "IdentificationResult", "IdentificationStatus",
    "MatchMethod", "CardLayout", "DatabaseUnavailable",
]
