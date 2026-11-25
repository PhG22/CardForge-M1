"""
Saneamento do texto lido pelo OCR na linha de informação do rodapé (CV-AD-04).

Os dois campos que identificam a impressão — código de edição e número de colecionador — são
curtos, impressos pequenos e sem nenhuma redundância: uma única troca de caractere já derruba a
identificação. O OCR confunde justamente os caracteres parecidos (O/0, I/1/L, S/5, B/8, J/I) e
cola palavras vizinhas, e nas cartas não inglesas o que vem colado ao código é a etiqueta de
idioma ("LCIPT", "MOM★PTSAMBURLEY").

Este módulo concentra o que é comum ao `InfoLineParser` e ao `SetCodeCatalog`:

* `CONFUSIONS` / `char_variants` — variantes de um código com uma letra trocada;
* `fix_digits` — conserta letras em grupos que já têm dígitos ("RO271" -> "R0271");
* `LANGUAGE_TAGS` / `split_language` — separa o código da etiqueta de idioma colada.

Nada aqui decide nada: tudo gera *candidatos*, que o `SetCodeCatalog` confere contra as edições
oficiais e o `CardMatcher` confere contra o `cards.db`. Um candidato errado simplesmente não
existe no banco.
"""

from __future__ import annotations

import re

# Pares que o OCR troca nos dois sentidos, em texto em caixa alta.
CONFUSION_PAIRS = ("1I", "1L", "JI", "0O", "0D", "0Q", "5S", "8B", "2Z", "6G", "4A", "7T", "UV", "VY")

CONFUSIONS: dict[str, str] = {}
for _a, _b in CONFUSION_PAIRS:
    CONFUSIONS[_a] = CONFUSIONS.get(_a, "") + _b
    CONFUSIONS[_b] = CONFUSIONS.get(_b, "") + _a

# Letras que, dentro de um número, são quase sempre o dígito parecido.
LETTER_TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "J": "1",
                   "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7", "A": "4"}

# Etiquetas de idioma impressas depois do código da edição.
LANGUAGE_TAGS = ("EN", "ENG", "PT", "ES", "FR", "DE", "IT", "JA", "JP", "KO", "RU",
                 "ZHS", "ZHT", "PH", "CT", "CS")
ENGLISH_TAGS = ("EN", "ENG")
# Comprimentos possíveis de um código de edição no Scryfall (3 a 5 na prática).
CODE_LENGTHS = (3, 4, 5)

_RUN_RE = re.compile(r"[A-Z0-9]+")


def char_variants(token: str) -> list[str]:
    """Variantes do token com UM caractere trocado por um parecido ("F1N" -> "FIN")."""
    out: list[str] = []
    for i, ch in enumerate(token):
        for alt in CONFUSIONS.get(ch, ""):
            out.append(token[:i] + alt + token[i + 1:])
    return out


def fix_digits(text: str) -> str:
    """
    Corrige letras confundidas com dígitos, só dentro de grupos que JÁ têm algum dígito:
    "RO271 LCIPT" -> "R0271 LCIPT" (o código da edição fica intacto porque não tem dígito).

    O resultado é sempre um *candidato a mais*, nunca a substituição da leitura original:
    a conversão também atrapalha em alguns casos ("L0277" -> "10277", onde o número se perde).
    """
    def convert(run: re.Match) -> str:
        chunk = run.group(0)
        if not any(ch.isdigit() for ch in chunk):
            return chunk
        return "".join(LETTER_TO_DIGIT.get(ch, ch) for ch in chunk)

    return _RUN_RE.sub(convert, text.upper())


def language_at(rest: str) -> str:
    """Etiqueta de idioma no começo de `rest` ("PTSAMBURLEY" -> "PT"), ou ""."""
    for tag in sorted(LANGUAGE_TAGS, key=len, reverse=True):
        if rest.startswith(tag):
            return tag
    return ""


def language_after(text: str, *anchors: str) -> str:
    """
    Etiqueta de idioma impressa logo depois do código da edição, como vem na carta:

        "IKO·PT STEVEELLIS"  "IKO·PTSTEVEELLIS"  "LCIPT MAURICIOCALLE"  "MOM★PTSAMBURLEY"  -> "PT"
        "FICENALEXANDREHONORE"                                                             -> "EN"

    Ancorar no código é o que torna isto seguro: duas letras soltas no meio do nome do artista
    ("ESTEBAN", "DEANE") não viram idioma, porque só conta o que vem imediatamente depois do código.
    Os âncoras são tentados em ordem — o código corrigido primeiro e, depois, o token como o OCR o
    leu, que é o que aparece no texto quando o próprio código saiu errado ("JKO·PT" -> IKO).
    A segunda passada tolera um caractere lido no lugar do separador (o ★ virando letra).
    """
    text = text.upper()
    for lenient in (False, True):
        for anchor in anchors:
            if not anchor:
                continue
            for m in re.finditer(re.escape(anchor.upper()), text):
                rest = re.sub(r"^[^A-Z0-9]{1,2}", "", text[m.end():])
                tag = nearby_language(rest) if lenient else language_at(rest)
                if tag:
                    return tag
    return ""


def nearby_language(rest: str) -> str:
    """
    Etiqueta de idioma logo depois do código, deslocada por um caractere lido errado:
    "APTSAMBURLEY" -> "PT" (o ★ de "MOM★PT" virou uma letra). Exige a etiqueta exata.
    """
    head = rest[:4]
    return next((tag for tag in LANGUAGE_TAGS if tag in head), "")


def misread_language(rest: str) -> bool:
    """
    `rest` é uma etiqueta de idioma com UMA letra errada ("PI", "FT" = "PT")?

    Só vale para restos curtos (até 4 caracteres): num resto longo o que vem depois do código é o
    nome do artista, não a etiqueta. E o idioma nunca é deduzido daqui — senão uma leitura ruim
    transformaria uma carta inglesa em carta "localizada".
    """
    if len(rest) > 4:
        return False
    for size in (3, 2):
        head = rest[:size]
        if len(head) < size:
            continue
        for tag in LANGUAGE_TAGS:
            if len(tag) == size and sum(a != b for a, b in zip(head, tag)) == 1:
                return True
    return False


# Qualidade da separação código/idioma, do melhor para o pior.
TAG_EXACT = 2      # etiqueta exata logo depois do código: "LCIPT"
TAG_NEARBY = 1     # etiqueta exata uma posição depois: "MOM★PT" lido como "MOMAPT"
TAG_MISREAD = 0    # etiqueta com uma letra errada: "LCIPI"


def split_language(token: str) -> list[tuple[str, str, int]]:
    """
    Separações possíveis de `token` em (código, idioma lido, qualidade).

    "LCIPT"                -> [("LCI", "PT", TAG_EXACT)]
    "MOMAPT"               -> [("MOM", "PT", TAG_NEARBY), ("MOMA", "PT", TAG_EXACT)]
    "FICENALEXANDREHONORE" -> [("FIC", "EN", TAG_EXACT)]
    "LCIPI"                -> [("LCI", "", TAG_MISREAD)]
    "IKO", "CHRISRAHN"     -> []

    O idioma vem vazio quando a etiqueta não foi lida de forma exata. Quem filtra os códigos que
    existem de verdade é o `SetCodeCatalog`.
    """
    out: list[tuple[str, str, int]] = []
    for size in CODE_LENGTHS:
        if len(token) <= size:
            continue
        rest = token[size:]
        tag = language_at(rest)
        if tag:
            out.append((token[:size], tag, TAG_EXACT))
            continue
        inner = nearby_language(rest)
        if inner:
            out.append((token[:size], inner, TAG_NEARBY))
        elif misread_language(rest):
            out.append((token[:size], "", TAG_MISREAD))
    return out
