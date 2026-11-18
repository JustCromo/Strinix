"""Righe di testo a colori che stanno sempre nella larghezza del terminale.

Una riga e' una lista di pezzi (ruolo, testo). `componi()` ripulisce ogni pezzo,
lo taglia alla larghezza disponibile e aggiunge i colori del tema.
"""

from __future__ import annotations

import unicodedata

from strinix.terminale import pulisci
from strinix.ui.tema import Tema

Pezzo = tuple[str, str]
Riga = list[Pezzo]


def larghezza_carattere(c: str) -> int:
    if unicodedata.combining(c):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def larghezza(testo: str) -> int:
    return sum(larghezza_carattere(c) for c in testo)


def taglia(testo: str, massimo: int, puntini: str = "…") -> str:
    """Taglia `testo` perche' occupi al massimo `massimo` colonne."""
    if massimo <= 0:
        return ""
    if larghezza(testo) <= massimo:
        return testo
    fuori, usate = [], 0
    limite = massimo - larghezza(puntini)
    for c in testo:
        w = larghezza_carattere(c)
        if usate + w > limite:
            break
        fuori.append(c)
        usate += w
    return "".join(fuori) + (puntini if limite >= 0 else "")


_ASCII = str.maketrans({"·": "-", "…": "~", "↑": "^", "↓": "v", "«": '"', "»": '"', "–": "-", "—": "-",
                        "’": "'", "‘": "'", "“": '"', "”": '"', "€": "E"})


def in_ascii(testo: str) -> str:
    """Versione solo ASCII per i terminali che non reggono Unicode (es. "è" -> "e", "·" -> "-")."""
    testo = unicodedata.normalize("NFKD", testo.translate(_ASCII))
    return "".join(c if c.isascii() else ("" if unicodedata.combining(c) else "?") for c in testo)


def pulita(riga: Riga) -> Riga:
    return [(ruolo, pulisci(testo, 10_000)) for ruolo, testo in riga]


def larghezza_riga(riga: Riga) -> int:
    return sum(larghezza(t) for _, t in riga)


def adatta(riga: Riga, colonne: int, puntini: str = "…") -> Riga:
    """Ripulisce e taglia la riga a `colonne`, poi la allunga con spazi fino a `colonne`."""
    fuori: Riga = []
    resto = colonne
    for ruolo, testo in pulita(riga):
        if resto <= 0:
            break
        if larghezza(testo) > resto:
            testo = taglia(testo, resto, puntini)
        fuori.append((ruolo, testo))
        resto -= larghezza(testo)
    if resto > 0:
        fuori.append(("testo", " " * resto))
    return fuori


def componi(riga: Riga, tema: Tema, colonne: int | None = None, sfondo: str | None = None) -> str:
    """Trasforma la riga in testo per il terminale (con colori se il tema li usa)."""
    if not tema.unicode:
        riga = [(r, in_ascii(pulisci(x, 10_000))) for r, x in riga]
    pezzi = adatta(riga, colonne, tema.simbolo("puntini")) if colonne is not None else pulita(riga)
    if not tema.colori:
        return "".join(t for _, t in pezzi)
    bg = tema.sgr_sfondo(sfondo) if sfondo else ""
    return "".join(f"{bg}{tema.sgr_testo(r)}{t}" for r, t in pezzi) + "\x1b[0m"
