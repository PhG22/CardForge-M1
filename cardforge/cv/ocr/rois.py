from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np


class CardLayout(str, Enum):
    STANDARD = "STANDARD"
    SAGA = "SAGA"
    FULL_ART = "FULL_ART"
    TOKEN_FULL = "TOKEN_FULL"
    TOKEN_CLASSIC = "TOKEN_CLASSIC"

    @property
    def is_token(self) -> bool:
        return self in (CardLayout.TOKEN_FULL, CardLayout.TOKEN_CLASSIC)


@dataclass(frozen=True)
class Roi:
    """Região de interesse na carta retificada de 630 × 880 px."""
    y1: int
    y2: int
    x1: int
    x2: int

    def crop(self, img: np.ndarray) -> np.ndarray:
        return img[self.y1:self.y2, self.x1:self.x2]

    def rect(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return (self.x1, self.y1), (self.x2, self.y2)


@dataclass(frozen=True)
class RoiSet:
    name: Roi
    type: Roi
    info: Roi


class RoiCatalog:
    """Um RoiSet por CardLayout (coordenadas calibradas na PoC)."""

    # Nome e info com margem extra (vs. PoC) para tolerar recortes ligeiramente desalinhados
    # (capa/sleeve, cantos arredondados): o título pode descer até y≈100 e o código da edição
    # começa em x≈15 — com x1=30 o "C" de "C21" e o "I" de "IKO" eram cortados.
    _NAME = Roi(22, 105, 30, 460)
    _INFO = Roi(805, 875, 10, 320)
    ROIS: dict[CardLayout, RoiSet] = {
        CardLayout.STANDARD:      RoiSet(_NAME, Roi(485, 555, 35, 490), _INFO),
        CardLayout.SAGA:          RoiSet(_NAME, Roi(675, 735, 35, 490), _INFO),
        CardLayout.FULL_ART:      RoiSet(_NAME, Roi(735, 795, 50, 490), _INFO),
        CardLayout.TOKEN_FULL:    RoiSet(_NAME, Roi(735, 795, 35, 490), _INFO),
        CardLayout.TOKEN_CLASSIC: RoiSet(_NAME, Roi(575, 635, 35, 490), _INFO),
    }

    # Faixa e zonas usadas pelo LayoutClassifier (uma só chamada de OCR).
    LAYOUT_BAND = Roi(565, 795, 30, 490)
    ZONE_CLASSIC_TOKEN = (565, 640)
    ZONE_SAGA = (670, 740)
    ZONE_FOOTER = (735, 795)

    @classmethod
    def rois_for(cls, layout: CardLayout) -> RoiSet:
        return cls.ROIS[layout]
