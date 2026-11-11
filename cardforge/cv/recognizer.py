from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from ..data.repository import CardRepository
from .capture.camera import Frame, sharpness
from .config import VisionConfig
from .detection import CardDetector, PerspectiveRectifier, fixed_quad
from .matching import CardMatcher, CollectorInfo, InfoLineParser, MatchMethod, MatchResult, NameIndex, SetCodeCatalog
from .ocr import (CardLayout, ExtractedFields, FaceDownDetector, FieldExtractor, LayoutClassifier, OcrEngine,
                  OrientationResolver, RoiCatalog)
from .result import IdentificationResult, IdentificationStatus


class CardRecognizer:
    """
    Pipeline de reconhecimento de UMA captura (CV-SD-01): borda + retificação → orientação →
    verso? → layout → OCR dos campos → set/número → match local. Nenhuma chamada de rede (RNF15).
    """

    def __init__(self, cfg: VisionConfig, ocr: OcrEngine, repo: CardRepository,
                 names: NameIndex, sets: SetCodeCatalog):
        self.cfg = cfg
        self.ocr = ocr
        self.detector = CardDetector(cfg)
        self.rectifier = PerspectiveRectifier(cfg)
        self.extractor = FieldExtractor(ocr, cfg.ocr_upscale)
        self.parser = InfoLineParser(sets, cfg.set_cutoff)
        self.orientation = OrientationResolver(self.extractor, names, cfg, self.parser)
        self.face_down = FaceDownDetector(cfg.face_down)
        self.layouts = LayoutClassifier(ocr)
        self.matcher = CardMatcher(repo, names, cfg)

    def recognize(self, frame: Frame | np.ndarray) -> IdentificationResult:
        t0 = time.perf_counter()
        calls0 = self.ocr.calls
        image = frame.image if isinstance(frame, Frame) else frame

        def done(result: IdentificationResult) -> IdentificationResult:
            result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            result.debug["ocr_calls"] = self.ocr.calls - calls0
            return result

        # 1. Onde está a carta: recorte fixo calibrado (máquina) ou detecção de bordas (outras fotos)
        if self.cfg.crop.mode == "fixed":
            quad = fixed_quad(self.cfg.crop, image.shape)
            failure = "crop not calibrated"
        else:
            quad = self.detector.detect(image)
            failure = "card border not found"
        if quad is None:
            return done(IdentificationResult(IdentificationStatus.CROP_FAILED,
                                             debug={"sharpness": round(sharpness(image), 1), "crop": failure}))
        card = self.rectifier.rectify(image, quad)
        debug = {"strategy": quad.strategy.value, "sharpness": round(sharpness(card.image), 1)}

        # 2. Orientação (0°/180°) e carta virada (RF39)
        orient = self.orientation.resolve(card)
        card = orient.card
        debug.update(orient.debug, rotated180=card.rotated180, title_score=orient.title_score)
        if self.face_down.is_face_down(orient):
            return done(IdentificationResult(IdentificationStatus.FACE_DOWN, rectified=card.image, debug=debug))

        # 3. Atalho: nome e info ficam no mesmo lugar em todos os layouts. Se a leitura com as ROIs padrão
        #    (já feita para a orientação) identifica a carta, o OCR de layout não mudaria o resultado.
        if self.cfg.skip_layout_when_identified and orient.fields is not None:
            first = self._identify(orient.fields, CardLayout.STANDARD)
            # Match só pelo nome com set+número lidos = o layout pode importar (tokens: "fic" -> "tfic").
            by_name_only = first.match.method in (MatchMethod.NAME_EXACT, MatchMethod.NAME_FUZZY)
            if (first.identified and not (by_name_only and first.info.has_set and first.info.has_number)
                    and not self.parser.has_token_mark(orient.fields.info_raw)):
                debug.update(name_score=first.debug_name_score, layout_ocr_skipped=True)
                return done(first.to_result(card.image, debug))
        else:
            first = None

        # 4. Layout (1 OCR) e campos nas ROIs desse layout (reaproveita a leitura STANDARD)
        layout = self.layouts.classify(card)
        if layout is CardLayout.STANDARD and first is not None:
            attempt = first
        else:
            fields = orient.fields if layout is CardLayout.STANDARD and orient.fields is not None else                 self.extractor.extract(card.image, RoiCatalog.rois_for(layout))
            attempt = self._identify(fields, layout)
        debug["name_score"] = attempt.debug_name_score
        return done(attempt.to_result(card.image, debug))

    def _identify(self, fields: ExtractedFields, layout: CardLayout) -> "_Attempt":
        """Set/número e match no banco local para um conjunto de campos lidos."""
        info = self.parser.parse(fields.info_raw)
        match = self.matcher.match(fields, info, layout)
        identified = match.confidence >= self.cfg.confidence_threshold and not match.needs_review
        return _Attempt(fields, info, layout, match, identified)


@dataclass
class _Attempt:
    fields: ExtractedFields
    info: CollectorInfo
    layout: CardLayout
    match: MatchResult
    identified: bool

    @property
    def debug_name_score(self) -> int:
        return self.match.name_score

    def to_result(self, rectified: np.ndarray, debug: dict) -> IdentificationResult:
        return IdentificationResult(
            status=IdentificationStatus.IDENTIFIED if self.identified else IdentificationStatus.LOW_CONFIDENCE,
            card=self.match.card,
            method=self.match.method,
            confidence=self.match.confidence,
            layout=self.layout,
            fields=self.fields,
            info=self.info,
            needs_review=self.match.needs_review,
            rectified=rectified,
            debug=debug,
        )
