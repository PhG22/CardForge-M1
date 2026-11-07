"""Picks the tray (bin) for an identified card.

Default rule (RF20, Appendix D): the card's Scryfall color identity.

    {W} -> 1   {U} -> 2   {B} -> 3   {R} -> 4   {G} -> 5
    colorless or multicolor -> 6 (overflow)

Unidentified (RF25) and face-down (RF39) cards also go to the overflow tray.
User-defined rules (RF21, optional) are not implemented: the app has no rules
screen yet. When they exist, check them here before the default mapping.
"""

from __future__ import annotations

OVERFLOW_TRAY = 6
COLOR_TRAYS = {"W": 1, "U": 2, "B": 3, "R": 4, "G": 5}


def tray_for_color_identity(color_identity: str | frozenset[str] | None) -> int:
    colors = set(color_identity or "")
    if len(colors) == 1:
        return COLOR_TRAYS.get(next(iter(colors)), OVERFLOW_TRAY)
    return OVERFLOW_TRAY
