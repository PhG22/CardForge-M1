from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Ordem canônica WUBRG, usada para exibir a identidade de cor de forma estável.
COLOR_ORDER = "WUBRG"
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


@dataclass(frozen=True)
class CardRecord:
    """
    Uma impressão (print) oficial de uma carta no espelho local do Scryfall.
    `print_id` é o id do Scryfall e serve como `card_identifier` no inventário (S-08).
    """
    print_id: str
    name: str
    mana_cost: str
    type_line: str
    color_identity: frozenset[str] = field(default_factory=frozenset)
    rarity: str = ""
    set_code: str = ""
    collector_number: str = ""
    layout: str = ""

    @property
    def color_identity_str(self) -> str:
        """Identidade de cor como string WUBRG ordenada ("" = incolor)."""
        return "".join(c for c in COLOR_ORDER if c in self.color_identity)

    @property
    def is_colorless(self) -> bool:
        return not self.color_identity

    @property
    def is_multicolor(self) -> bool:
        return len(self.color_identity) > 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d["color_identity"] = self.color_identity_str
        return d
