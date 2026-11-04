"""Pacote cardforge.cv.matching — linha de info, códigos de edição e match no cards.db (CV-CL-03)."""

from .info_parser import NA, UNKNOWN, CollectorInfo, InfoLineParser
from .matcher import CardMatcher, MatchMethod, MatchResult
from .name_index import NameIndex, compact
from .set_catalog import SetCodeCatalog

__all__ = [
    "NA", "UNKNOWN", "CollectorInfo", "InfoLineParser", "CardMatcher", "MatchMethod", "MatchResult",
    "NameIndex", "compact", "SetCodeCatalog",
]
