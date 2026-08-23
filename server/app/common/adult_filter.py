"""Felnőtt (XXX) tartalom felismerése a szabadszöveges kereséshez.

A rendszerbeállítások `filter_adult` kapcsolója vezérli (alapból BE);
kikapcsolható az admin felület Rendszer oldalán.
"""

import re

# Szó-szinten illesztett kulcsszavak (kis/nagybetű független). Szándékosan
# rövid lista: a token-alapú illesztés miatt a "analysis"/"Sussex" jellegű
# álpozitívak kizárva, de pl. a "Sex Education" sorozat is fennakadhat —
# ilyenkor a kapcsoló kikapcsolható.
_ADULT_TOKENS = {
    "xxx",
    "porn",
    "porno",
    "pornó",
    "pornstar",
    "brazzers",
    "onlyfans",
    "milf",
    "hentai",
    "szex",
    "sex",
    "erotic",
    "erotik",
    "erotika",
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9áéíóöőúüű]+")


def is_adult_name(name: str) -> bool:
    """Igaz, ha a torrent neve felnőtt tartalomra utal."""
    tokens = _TOKEN_SPLIT.split(name.lower())
    return any(token in _ADULT_TOKENS for token in tokens)


def is_adult_ncore_category(category: str) -> bool:
    """Igaz az nCore xxx_* kategóriáira."""
    return category.startswith("xxx")
