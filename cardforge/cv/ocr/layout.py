from __future__ import annotations

from ..detection.rectifier import RectifiedCard
from .engine import OcrEngine
from .rois import CardLayout, RoiCatalog

_LAND_WORDS = ("land", "terreno", "tierra", "terrain")
_TOKEN_WORDS = ("token", "ficha")


class LayoutClassifier:
    """
    Classifica o layout pela faixa y 565–795, distribuindo as linhas pelas zonas (token clássico
    565–640, saga 670–740, rodapé 735–795) pelo centro Y (CV-SD-02b, passo 4). Só é chamado quando a
    leitura com as ROIs padrão não bastou para identificar a carta (ver CardRecognizer).
    """

    def __init__(self, ocr: OcrEngine):
        self.ocr = ocr

    def classify(self, card: RectifiedCard) -> CardLayout:
        band = RoiCatalog.LAYOUT_BAND
        lines = [(ln.y_center() + band.y1, ln.text) for ln in self.ocr.read(band.crop(card.image))]
        return self.classify_lines(lines)

    def classify_lines(self, lines: list[tuple[float, str]]) -> CardLayout:
        """Distribui linhas (y na carta, texto) pelas zonas e decide o layout — sem OCR."""
        zones = {"classic": [], "saga": [], "footer": []}
        for y, text in lines:
            text = text.lower()
            if RoiCatalog.ZONE_CLASSIC_TOKEN[0] <= y <= RoiCatalog.ZONE_CLASSIC_TOKEN[1]:
                zones["classic"].append(text)
            if RoiCatalog.ZONE_SAGA[0] <= y <= RoiCatalog.ZONE_SAGA[1]:
                zones["saga"].append(text)
            if RoiCatalog.ZONE_FOOTER[0] <= y <= RoiCatalog.ZONE_FOOTER[1]:
                zones["footer"].append(text)
        return self.decide(*(" ".join(zones[k]) for k in ("classic", "saga", "footer")))

    @staticmethod
    def decide(classic: str, saga: str, footer: str) -> CardLayout:
        if "saga" in saga:
            return CardLayout.SAGA
        if any(w in footer for w in _LAND_WORDS):
            return CardLayout.FULL_ART
        if any(w in footer for w in _TOKEN_WORDS):
            return CardLayout.TOKEN_FULL
        if any(w in classic for w in _TOKEN_WORDS):
            return CardLayout.TOKEN_CLASSIC
        return CardLayout.STANDARD
