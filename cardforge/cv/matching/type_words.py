from __future__ import annotations

import re
import unicodedata

# Tipos principais de carta, por idioma impresso (PT/ES/FR/DE/IT + EN), sem acentos e em minúsculas.
_TYPE_WORDS = {
    "creature": ("creature", "criatura", "creature", "kreatur", "creatura"),
    "land": ("land", "terreno", "tierra", "terrain", "terra"),
    "instant": ("instant", "instantanea", "instantaneo", "ephemere", "spontanzauber", "istantaneo"),
    "sorcery": ("sorcery", "feitico", "conjuro", "rituel", "hexerei", "stregoneria"),
    "enchantment": ("enchantment", "encantamento", "encantamiento", "enchantement", "verzauberung",
                    "incantesimo"),
    "artifact": ("artifact", "artefato", "artefacto", "artefact", "artefakt", "artefatto"),
    "planeswalker": ("planeswalker",),
    "battle": ("battle", "batalha", "batalla", "bataille", "schlacht", "battaglia"),
}
_WORD_TO_TYPE = {w: t for t, words in _TYPE_WORDS.items() for w in words}


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.findall(r"[a-z]+", text)


def card_types(type_line: str) -> set[str]:
    """
    Tipos principais encontrados numa linha de tipo, em qualquer idioma suportado.
    O OCR às vezes cola palavras ("EnchantmentCreature"), por isso procura também por substring.
    """
    found: set[str] = set()
    for word in _words(type_line.split("//")[0]):
        if word in _WORD_TO_TYPE:
            found.add(_WORD_TO_TYPE[word])
            continue
        for w, t in _WORD_TO_TYPE.items():
            if len(w) >= 5 and w in word:
                found.add(t)
    return found


def types_agree(ocr_type_line: str, official_type_line: str) -> bool:
    """Os tipos lidos (não vazios) são todos tipos da carta oficial?"""
    ocr = card_types(ocr_type_line)
    return bool(ocr) and ocr <= card_types(official_type_line)
