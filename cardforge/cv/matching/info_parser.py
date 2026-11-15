from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ocr_text import (ENGLISH_TAGS, LANGUAGE_TAGS, TAG_EXACT, TAG_MISREAD, TAG_NEARBY, fix_digits,
                       language_after, split_language)
from .set_catalog import SetCodeCatalog

UNKNOWN = "UNKNOWN"
NA = "N/A"

# Mantido para compatibilidade: a lista canônica está em ocr_text.LANGUAGE_TAGS.
LANGUAGE_CODES = LANGUAGE_TAGS

_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?:/\d+)?(?!\d)")
# "236/249" é inequívoco; em molduras antigas a linha também traz o ano do copyright ("©2013").
# Raridade "T" colada ao número ("T0002") = token: o set dele no Scryfall é outro ("fic" -> "tfic").
_TOKEN_MARK_RE = re.compile(r"(?<![A-Z0-9])T\s?\d{3,4}(?!\d)")
_NUMBER_OF_TOTAL_RE = re.compile(r"(?<!\d)(\d{1,4})/\d{1,4}(?!\d)")

# Pontuação de um candidato a código de edição, do mais confiável para o menos.
_SCORE_BY_TAG = {TAG_EXACT: 100, TAG_NEARBY: 95, TAG_MISREAD: 88}
_SCORE_WHOLE_TOKEN = 100


@dataclass
class CollectorInfo:
    set_code: str = NA            # maiúsculas, ex.: "FIC" — o melhor candidato
    collector_number: str = UNKNOWN
    set_score: int = 0            # 100 = exato, < 100 = saneado/fuzzy
    language: str = ""            # etiqueta de idioma lida junto ao código ("PT", "EN"...), "" = nenhuma
    # Alternativas ordenadas (melhor primeiro) para o CardMatcher conferir no cards.db: uma troca de
    # caractere do OCR não derruba mais a identificação, porque o candidato errado não existe no banco.
    set_candidates: tuple[str, ...] = field(default_factory=tuple)
    number_candidates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_set(self) -> bool:
        return self.set_code != NA

    @property
    def has_number(self) -> bool:
        return self.collector_number != UNKNOWN

    @property
    def is_localized(self) -> bool:
        """A linha de info traz uma etiqueta de idioma que não é inglês (lida de forma exata)."""
        return bool(self.language) and self.language not in ENGLISH_TAGS


class InfoLineParser:
    """
    Extrai número de colecionador e código de edição da linha de informação do rodapé (CV-AD-04).

    A linha é curta e sem redundância, e o OCR troca caracteres parecidos (O/0, I/1/L, J/I) e cola
    a etiqueta de idioma ao código ("LCIPT", "MOM★PTSAMBURLEY"). Em vez de apostar numa leitura,
    `parse` devolve os candidatos ordenados — quem confere qual existe de verdade é o `CardMatcher`,
    cruzando edição + número no `cards.db`.
    """

    def __init__(self, sets: SetCodeCatalog, set_cutoff: int = 85):
        self.sets = sets
        self.set_cutoff = set_cutoff

    # ----------------------------------------------------------------- número
    @staticmethod
    def number_candidates(raw: str) -> tuple[str, ...]:
        """
        Números de colecionador possíveis, melhor primeiro.

        "236/249" tem prioridade sobre um número solto (o ano do copyright das molduras antigas).
        Depois entra a leitura com os dígitos saneados ("RO271" -> "R0271", "1OO" -> "100"), que
        serve de alternativa: ela conserta uns casos e estraga outros ("L0277" -> "10277").
        """
        texts = [raw.upper()]
        fixed = fix_digits(raw)
        if fixed != texts[0]:
            texts.append(fixed)
        found: list[str] = []
        for pattern in (_NUMBER_OF_TOTAL_RE, _NUMBER_RE):
            for text in texts:
                m = pattern.search(text)
                if m and m.group(1) not in found:
                    found.append(m.group(1))
        # Um número de 1–2 dígitos que é só o começo de outro candidato costuma ser leitura cortada
        # ("1OO": o cru dá "1", o saneado dá "100"), então vai para o fim da fila.
        return tuple(sorted(found, key=lambda n: any(o != n and o.lstrip("0").startswith(n.lstrip("0"))
                                                     and len(o) > len(n) for o in found)))

    @classmethod
    def collector_number(cls, raw: str) -> str:
        cands = cls.number_candidates(raw)
        return cands[0] if cands else UNKNOWN

    @staticmethod
    def has_token_mark(raw: str) -> bool:
        return bool(_TOKEN_MARK_RE.search(raw.upper()))

    # ----------------------------------------------------------------- edição
    @staticmethod
    def _tokens(raw: str) -> list[str]:
        return [t for t in re.split(r"[^A-Z0-9]", raw.upper()) if t]

    def set_tokens(self, raw: str) -> list[str]:
        """Tokens candidatos a código de edição (+ variantes sem a etiqueta de idioma: "FICEN" -> "FIC")."""
        out: list[str] = []
        for tok in self._tokens(raw):
            for cand in [tok] + [code for code, _, _ in split_language(tok)]:
                if 3 <= len(cand) <= 6 and not cand.isdigit() and cand not in LANGUAGE_TAGS and cand not in out:
                    out.append(cand)
        return out

    def _catalog_candidates(self, tokens: list[str]) -> list[tuple[str, int, str]]:
        """
        Códigos que existem no catálogo, do melhor para o pior, como (código, pontuação, token lido).
        O token é guardado porque é ele que aparece na linha de info — serve de âncora para achar a
        etiqueta de idioma mesmo quando o código em si foi corrigido.
        """
        scored: dict[str, tuple[int, str]] = {}

        def record(code: str, score: int, token: str) -> None:
            if score > scored.get(code, (-1, ""))[0]:
                scored[code] = (score, token)

        for tok in tokens:
            if tok in self.sets:
                record(tok, _SCORE_WHOLE_TOKEN, tok)
            for code, _tag, quality in split_language(tok):
                if code in self.sets:
                    record(code, _SCORE_BY_TAG[quality], tok)
        return [(code, score, token) for code, (score, token) in
                sorted(scored.items(), key=lambda kv: -kv[1][0])]

    def _fuzzy_candidates(self, tokens: list[str]) -> list[tuple[str, int, str]]:
        """Último recurso: uma letra trocada (90) ou parecido (>= set_cutoff) em qualquer token."""
        scored: dict[str, tuple[int, str]] = {}
        for tok in self.set_tokens(" ".join(tokens)):
            hit = self.sets.fuzzy_match(tok, self.set_cutoff)
            if hit and hit[1] > scored.get(hit[0], (-1, ""))[0]:
                scored[hit[0]] = (hit[1], tok)
        return [(code, score, token) for code, (score, token) in
                sorted(scored.items(), key=lambda kv: -kv[1][0])]

    def parse(self, info_raw: str) -> CollectorInfo:
        tokens = self._tokens(info_raw)
        numbers = self.number_candidates(info_raw)
        catalog = self._catalog_candidates(tokens)
        # Os parecidos entram como alternativa (o cruzamento com o número no banco descarta os
        # errados). A fila é só por pontuação; entre empates, o que veio do catálogo vem primeiro.
        found = {code for code, _, _ in catalog}
        ranked = sorted(catalog + [c for c in self._fuzzy_candidates(tokens) if c[0] not in found],
                        key=lambda cand: -cand[1])
        number = numbers[0] if numbers else UNKNOWN
        if not ranked:
            return CollectorInfo(NA, number, 0, "", (), numbers)
        best, score, token = ranked[0]
        return CollectorInfo(best, number, score, language_after(info_raw, best, token),
                             tuple(code for code, _, _ in ranked), numbers)
