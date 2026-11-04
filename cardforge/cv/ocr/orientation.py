from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import VisionConfig
from ..detection.rectifier import RectifiedCard
from ..matching.info_parser import InfoLineParser
from ..matching.name_index import NameIndex
from .fields import ExtractedFields, FieldExtractor
from .rois import CardLayout, RoiCatalog


@dataclass
class OrientationResult:
    card: RectifiedCard
    title_score: int                       # 0–100: quão bem o título lido bate com um nome oficial
    title_chars: int                        # caracteres alfanuméricos do título na orientação escolhida
    # Campos lidos com as ROIs STANDARD na orientação escolhida — se já identificam a carta, o OCR de
    # layout é dispensado; senão são reaproveitados quando o layout for STANDARD.
    fields: Optional[ExtractedFields] = None
    tried_rotation: bool = False
    rotated_title_chars: int = 0
    debug: dict = field(default_factory=dict)


class OrientationResolver:
    """
    Decide entre 0° e 180° pela pontuação do título contra a lista de nomes (CV-AD-03):
    só tenta a rotação se a leitura normal pontuar < 75, e só a adota se ganhar por > 10 pontos.

    Evidência extra: uma linha de info com código de edição oficial exato + número só é lida
    na orientação certa. Cartas noutro idioma (título nunca bate com nomes ingleses) e títulos
    mal lidos não provocam rotações erradas nem uma chamada de OCR a mais.
    """

    def __init__(self, extractor: FieldExtractor, names: NameIndex, cfg: VisionConfig,
                 parser: Optional[InfoLineParser] = None):
        self.extractor = extractor
        self.names = names
        self.cfg = cfg
        self.parser = parser

    def title_score(self, fields: ExtractedFields) -> int:
        return self.names.best_score(fields.name)

    def info_ok(self, fields: ExtractedFields) -> bool:
        if self.parser is None:
            return False
        info = self.parser.parse(fields.info_raw)
        return info.set_score == 100 and info.has_number

    def resolve(self, card: RectifiedCard) -> OrientationResult:
        rois = RoiCatalog.rois_for(CardLayout.STANDARD)
        normal = self.extractor.extract(card.image, rois)
        score_normal = self.title_score(normal)
        result = OrientationResult(card, score_normal, normal.title_chars, normal)
        info_normal = self.info_ok(normal)
        if score_normal >= self.cfg.orientation_threshold or info_normal:
            return result

        rotated = card.rotated()
        flipped = self.extractor.extract(rotated.image, rois)
        score_rotated = self.title_score(flipped)
        info_rotated = self.info_ok(flipped)
        result.tried_rotation = True
        result.rotated_title_chars = flipped.title_chars
        result.debug = {"score_normal": score_normal, "score_rotated": score_rotated,
                        "info_rotated": info_rotated}
        if info_rotated or score_rotated > score_normal + self.cfg.orientation_margin:
            return OrientationResult(rotated, score_rotated, flipped.title_chars, flipped, True,
                                     normal.title_chars, result.debug)
        return result
