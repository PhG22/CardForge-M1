from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from rapidfuzz import fuzz

from ...data.records import CardRecord
from ...data.repository import CardRepository
from ..config import VisionConfig
from ..ocr.fields import ExtractedFields
from ..ocr.rois import CardLayout
from .info_parser import CollectorInfo
from .name_index import NameIndex, compact
from .type_words import types_agree


class MatchMethod(str, Enum):
    SET_NUMBER = "SET_NUMBER"
    SET_NUMBER_LOCALIZED = "SET_NUMBER_LOCALIZED"   # set+número confirmados pelo tipo (carta não inglesa)
    NAME_EXACT = "NAME_EXACT"
    NAME_FUZZY = "NAME_FUZZY"
    NAME_SET_MISMATCH = "NAME_SET_MISMATCH"
    SET_NUMBER_NAME_MISMATCH = "SET_NUMBER_NAME_MISMATCH"
    NONE = "NONE"


@dataclass
class MatchResult:
    card: Optional[CardRecord]
    method: MatchMethod
    confidence: int
    needs_review: bool = False
    name_key: Optional[str] = None
    name_score: int = 0


class CardMatcher:
    """
    Converte os campos lidos numa carta oficial (CV-SD-03 / CV-AD-04):
    set + número (cruzado com o nome lido) → nome exato → nome fuzzy.
    Casos com divergência ficam com needs_review e abaixo do limiar de 90 % (RF18),
    para que o controle refaça a captura (RF29) e, por fim, marque a carta como não identificada.
    """

    def __init__(self, repo: CardRepository, names: NameIndex, cfg: VisionConfig):
        self.repo = repo
        self.names = names
        self.cfg = cfg

    def cross_check(self, ocr_name: str, official_name: str) -> bool:
        """O nome lido confirma a impressão encontrada por set + número?"""
        a, b = compact(ocr_name), compact(official_name)
        if not a:
            return True  # nada lido no título: não há como contradizer
        # Cartas de várias faces: basta bater com uma face.
        faces = [compact(f) for f in official_name.split(" // ")] + [b]
        return any(a in f or f in a or fuzz.ratio(a, f) >= self.cfg.cross_check_cutoff for f in faces if f)

    def match(self, fields: ExtractedFields, info: CollectorInfo, layout: CardLayout) -> MatchResult:
        set_query = info.set_code.lower()
        if layout.is_token and info.has_set and not set_query.startswith("t"):
            set_query = "t" + set_query  # tokens ficam em sets próprios no Scryfall ("fin" -> "tfin")

        name_key, name_score = self.names.best_name(fields.name, self.cfg.name_cutoff)

        printed: Optional[CardRecord] = None
        if info.has_set and info.has_number:
            printed = self.repo.find_print(set_query, info.collector_number)
            if printed is None and layout.is_token:
                printed = self.repo.find_print(info.set_code.lower(), info.collector_number)

        if printed is not None:
            if self.cross_check(fields.name, printed.name):
                return MatchResult(printed, MatchMethod.SET_NUMBER, 100, False, name_key, name_score)
            if name_key:
                by_name = self.repo.find_latest_print_by_name(name_key, set_query if info.has_set else "")
                if by_name is not None and by_name.name == printed.name:
                    # O nome lido é outro nome da MESMA carta (face, nome alternativo de Universes Beyond).
                    return MatchResult(printed, MatchMethod.SET_NUMBER, min(100, name_score), False,
                                       name_key, name_score)
                if by_name is not None:
                    return MatchResult(by_name, MatchMethod.NAME_SET_MISMATCH,
                                       min(name_score, self.cfg.confidence_threshold - 1), True,
                                       name_key, name_score)
            # Carta impressa noutro idioma ("Sagui Espinhoso"): o nome não corresponde a nenhuma carta
            # inglesa, então a confirmação vem dos tipos da carta lidos na linha de tipo ("Criatura").
            if (self.cfg.accept_localized_cards and len(compact(fields.name)) >= 3
                    and types_agree(fields.type, printed.type_line)):
                return MatchResult(printed, MatchMethod.SET_NUMBER_LOCALIZED, self.cfg.confidence_threshold,
                                   False, None, name_score)
            return MatchResult(printed, MatchMethod.SET_NUMBER_NAME_MISMATCH,
                               self.cfg.confidence_threshold - 1, True, name_key, name_score)

        if name_key:
            by_name = self.repo.find_latest_print_by_name(name_key, set_query if info.has_set else "")
            if by_name is not None:
                method = MatchMethod.NAME_EXACT if name_score == 100 else MatchMethod.NAME_FUZZY
                return MatchResult(by_name, method, name_score, False, name_key, name_score)

        return MatchResult(None, MatchMethod.NONE, 0, False, name_key, name_score)
