from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .engine import OcrEngine, OcrLine
from .rois import RoiSet

_GAP = 20  # px entre as faixas empilhadas


@dataclass
class ExtractedFields:
    name: str = ""
    type: str = ""
    info_raw: str = ""
    name_score: float = 0.0   # confiança média do OCR na linha do nome (diagnóstico)

    @property
    def title_chars(self) -> int:
        return sum(ch.isalnum() for ch in self.name)


class FieldExtractor:
    """
    Lê Nome, Tipo e Info numa única chamada de OCR: as três ROIs são empilhadas verticalmente
    e cada linha reconhecida é atribuída ao campo pela coordenada Y do seu centro (CV-AD-03).
    """

    def __init__(self, ocr: OcrEngine, upscale: float = 1.5):
        self.ocr = ocr
        self.upscale = upscale

    @staticmethod
    def stack(imgs: list[np.ndarray]) -> tuple[np.ndarray, list[int]]:
        """Empilha as imagens com largura comum (preenchimento preto). Devolve a imagem e os limites Y."""
        max_w = max(i.shape[1] for i in imgs)
        padded, bounds, y = [], [], 0
        for k, img in enumerate(imgs):
            bottom = _GAP if k < len(imgs) - 1 else 0
            padded.append(cv2.copyMakeBorder(img, 0, bottom, 0, max_w - img.shape[1],
                                             cv2.BORDER_CONSTANT, value=(0, 0, 0)))
            y += img.shape[0]
            bounds.append(y + _GAP // 2)  # meio do espaço entre faixas
            y += bottom
        return cv2.vconcat(padded), bounds

    def extract(self, image: np.ndarray, rois: RoiSet) -> ExtractedFields:
        batch, bounds = self.stack([rois.name.crop(image), rois.type.crop(image), rois.info.crop(image)])
        gray = cv2.cvtColor(batch, cv2.COLOR_BGR2GRAY)
        scaled = cv2.resize(gray, None, fx=self.upscale, fy=self.upscale, interpolation=cv2.INTER_LINEAR)
        lines = self.ocr.read(cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR))
        return self.assign(lines, bounds, self.upscale)

    @staticmethod
    def assign(lines: list[OcrLine], bounds: list[int], scale: float) -> ExtractedFields:
        buckets: list[list[OcrLine]] = [[], [], []]
        # Ordem de leitura: por linha (faixas de ~15 px) e, dentro dela, da esquerda para a direita.
        for line in sorted(lines, key=lambda ln: (round(ln.y_center() / scale / 15), ln.box[0][0])):
            y = line.y_center() / scale
            idx = 0 if y < bounds[0] else 1 if y < bounds[1] else 2
            buckets[idx].append(line)
        name, typ, info = (" ".join(ln.text for ln in b).strip() for b in buckets)
        name_score = float(np.mean([ln.score for ln in buckets[0]])) if buckets[0] else 0.0
        return ExtractedFields(name, typ, info, name_score)
