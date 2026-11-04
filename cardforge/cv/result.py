from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np

    from ..data.records import CardRecord
    from .matching.info_parser import CollectorInfo
    from .matching.matcher import MatchMethod
    from .ocr.fields import ExtractedFields
    from .ocr.rois import CardLayout


class IdentificationStatus(str, Enum):
    """Resultado de UMA chamada identify(). O loop de retentativas é do programa de controle (FW-AD-01)."""
    IDENTIFIED = "IDENTIFIED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    CROP_FAILED = "CROP_FAILED"
    FACE_DOWN = "FACE_DOWN"

    @property
    def is_final(self) -> bool:
        """IDENTIFIED e FACE_DOWN encerram as retentativas; os outros pedem nova captura com mais luz (RNF09)."""
        return self in (IdentificationStatus.IDENTIFIED, IdentificationStatus.FACE_DOWN)


@dataclass
class IdentificationResult:
    status: IdentificationStatus
    card: Optional["CardRecord"] = None
    method: Optional["MatchMethod"] = None
    confidence: int = 0
    layout: Optional["CardLayout"] = None
    fields: Optional["ExtractedFields"] = None
    info: Optional["CollectorInfo"] = None
    needs_review: bool = False
    elapsed_ms: int = 0
    # Diagnóstico (não persistido): imagem retificada, estratégia de contorno, nitidez, etc.
    rectified: Optional["np.ndarray"] = field(default=None, repr=False)
    debug: dict = field(default_factory=dict, repr=False)

    @property
    def identified(self) -> bool:
        return self.status is IdentificationStatus.IDENTIFIED

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "card": self.card.to_dict() if self.card else None,
            "method": self.method.value if self.method else None,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
            "layout": self.layout.value if self.layout else None,
            "ocr": {
                "name": self.fields.name if self.fields else "",
                "type": self.fields.type if self.fields else "",
                "info_raw": self.fields.info_raw if self.fields else "",
                "set_code": self.info.set_code if self.info else None,
                "collector_number": self.info.collector_number if self.info else None,
            },
            "elapsed_ms": self.elapsed_ms,
            "debug": {k: v for k, v in self.debug.items() if isinstance(v, (str, int, float, bool))},
        }
