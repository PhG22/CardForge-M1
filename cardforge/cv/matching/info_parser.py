from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .set_catalog import SetCodeCatalog

UNKNOWN = "UNKNOWN"
NA = "N/A"

LANGUAGE_CODES = ("EN", "ENG", "PT", "ES", "FR", "DE", "IT", "JP", "JA", "KO", "RU", "ZHS", "ZHT", "PH")
_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?:/\d+)?(?!\d)")
# "236/249" é inequívoco; em molduras antigas a linha também traz o ano do copyright ("©2013").
# Raridade "T" colada ao número ("T0002") = token: o set dele no Scryfall é outro ("fic" -> "tfic").
_TOKEN_MARK_RE = re.compile(r"(?<![A-Z0-9])T\s?\d{3,4}(?!\d)")
_NUMBER_OF_TOTAL_RE = re.compile(r"(?<!\d)(\d{1,4})/\d{1,4}(?!\d)")


@dataclass
class CollectorInfo:
    set_code: str = NA            # maiúsculas, ex.: "FIC"
    collector_number: str = UNKNOWN
    set_score: int = 0            # 100 = exato, < 100 = fuzzy

    @property
    def has_set(self) -> bool:
        return self.set_code != NA

    @property
    def has_number(self) -> bool:
        return self.collector_number != UNKNOWN


class InfoLineParser:
    """Extrai número de colecionador e código de edição da linha de informação do rodapé (CV-AD-04)."""

    def __init__(self, sets: SetCodeCatalog, set_cutoff: int = 85):
        self.sets = sets
        self.set_cutoff = set_cutoff

    @staticmethod
    def collector_number(raw: str) -> str:
        raw = raw.upper()
        m = _NUMBER_OF_TOTAL_RE.search(raw) or _NUMBER_RE.search(raw)
        return m.group(1) if m else UNKNOWN

    @staticmethod
    def has_token_mark(raw: str) -> bool:
        return bool(_TOKEN_MARK_RE.search(raw.upper()))

    @staticmethod
    def set_tokens(raw: str) -> list[str]:
        """Tokens candidatos a código de edição (+ variantes sem o sufixo de idioma: "FICEN" -> "FIC")."""
        tokens: list[str] = []
        for tok in re.split(r"[^A-Z0-9]", raw.upper()):
            # O OCR costuma colar o idioma (e o que vem depois) ao código: "FICEN", "TLEENBI".
            variants = [tok] + [tok[:i] for lang in LANGUAGE_CODES
                                for i in [tok.find(lang, 3)] if i >= 3]
            for t in variants:
                if 3 <= len(t) <= 6 and not t.isdigit() and t not in LANGUAGE_CODES and t not in tokens:
                    tokens.append(t)
        return tokens

    def parse(self, info_raw: str) -> CollectorInfo:
        number = self.collector_number(info_raw)
        tokens = self.set_tokens(info_raw)
        exact = self.sets.first_exact(tokens)
        if exact:
            return CollectorInfo(exact, number, 100)
        best: Optional[tuple[str, int]] = None
        for t in tokens:
            hit = self.sets.fuzzy_match(t, self.set_cutoff)
            if hit and (best is None or hit[1] > best[1]):
                best = hit
        if best:
            return CollectorInfo(best[0], number, best[1])
        return CollectorInfo(NA, number, 0)
