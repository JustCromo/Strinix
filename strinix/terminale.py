"""Colori e pulizia del testo per il terminale.

Tutto quello che arriva da fuori (configurazione, nomi di interfacce, e piu' avanti
i pacchetti di rete) passa da `pulisci()` prima di essere stampato: cosi' nessuno
puo' nascondere sequenze di escape che cambiano o falsano quello che vedi a schermo.
"""

from __future__ import annotations

import os
import re

# caratteri di controllo C0, DEL e C1 (compreso ESC, che apre le sequenze ANSI)
_CONTROLLO = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX = 200

COLORI = {
    "ok": "32",        # verde
    "avviso": "33",    # giallo
    "errore": "31",    # rosso
    "tenue": "90",     # grigio
    "titolo": "1;36",  # ciano in grassetto
}


def pulisci(testo: object, massimo: int = _MAX) -> str:
    """Rende sicuro da stampare un valore non fidato: niente caratteri di controllo, lunghezza limitata."""
    s = _CONTROLLO.sub("?", str(testo))
    return s if len(s) <= massimo else s[: massimo - 1] + "…"


def colori_attivi(stream, richiesti: bool = True) -> bool:
    """Colori solo su un terminale vero, e mai se l'utente ha impostato NO_COLOR."""
    if not richiesti or "NO_COLOR" in os.environ:
        return False
    if os.environ.get("TERM", "dumb") in ("", "dumb"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Stile:
    def __init__(self, attivo: bool) -> None:
        self.attivo = attivo

    def __call__(self, ruolo: str, testo: str) -> str:
        codice = COLORI.get(ruolo)
        if not self.attivo or codice is None:
            return testo
        return f"\x1b[{codice}m{testo}\x1b[0m"
