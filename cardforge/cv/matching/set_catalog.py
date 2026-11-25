from __future__ import annotations

from typing import Iterable, Optional

from rapidfuzz import fuzz, process

from .ocr_text import char_variants


class SetCodeCatalog:
    """Códigos de edição oficiais (ex.: "FIN", "MH3"), carregados de mtg_set."""

    def __init__(self, codes: Iterable[str] = ()):
        self.codes: set[str] = set()
        self._list: list[str] = []
        self.load(codes)

    def load(self, codes: Iterable[str]) -> None:
        self.codes = {c.upper() for c in codes if c}
        self._list = sorted(self.codes)

    def __contains__(self, code: str) -> bool:
        return code.upper() in self.codes

    def first_exact(self, tokens: Iterable[str]) -> Optional[str]:
        for t in tokens:
            if t in self.codes:
                return t
        return None

    def fuzzy_match(self, token: str, cutoff: int = 85) -> Optional[tuple[str, int]]:
        """
        Tolera uma letra errada pelo OCR (ex.: "F1N" -> "FIN"). Primeiro tenta as trocas típicas de
        OCR (`ocr_text.CONFUSION_PAIRS`) — num código de 3 letras, uma letra errada dá só 67 no ratio.
        """
        best: Optional[tuple[str, int]] = None
        for variant in char_variants(token):
            if variant in self.codes and (best is None or variant < best[0]):
                best = (variant, 90)
        if best and best[1] >= cutoff:
            return best
        hit = process.extractOne(token, self._list, scorer=fuzz.ratio, score_cutoff=cutoff)
        return (hit[0], int(round(hit[1]))) if hit else None
