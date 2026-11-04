from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

from rapidfuzz import fuzz, process


def compact(text: str) -> str:
    """
    Forma de comparação de nomes: minúsculas, sem acentos nem pontuação/espaços.
    O OCR costuma juntar palavras ("Tifa,MartialArtist") e trocar pontuação.
    """
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text)


class NameIndex:
    """Lista de nomes oficiais em memória para busca exata/fuzzy (só os nomes — CV-DP, Pi 3B com 1 GB)."""

    def __init__(self, names: Iterable[str] = ()):
        self.names: list[str] = []
        self._compact: list[str] = []
        self._by_compact: dict[str, str] = {}
        self.build(names)

    def build(self, names: Iterable[str]) -> None:
        self.names = sorted(set(n.lower() for n in names if n))
        self._by_compact = {}
        for n in self.names:
            self._by_compact.setdefault(compact(n), n)
        self._compact = list(self._by_compact.keys())

    def __len__(self) -> int:
        return len(self.names)

    def best_name(self, name: str, cutoff: int = 85) -> tuple[Optional[str], int]:
        """Devolve (name_key, pontuação 0–100) ou (None, 0). Exato = 100; fuzzy só com mais de 3 caracteres."""
        key = compact(name)
        if not key:
            return None, 0
        if key in self._by_compact:
            return self._by_compact[key], 100
        if len(key) <= 3:
            return None, 0
        hit = process.extractOne(key, self._compact, scorer=fuzz.ratio, score_cutoff=cutoff)
        if hit is None:
            return None, 0
        return self._by_compact[hit[0]], int(round(hit[1]))

    def best_score(self, name: str) -> int:
        """Pontuação do melhor nome (0 se menos de 3 caracteres) — usada para decidir a orientação."""
        if len(compact(name)) < 3:
            return 0
        return self.best_name(name, cutoff=0)[1]
