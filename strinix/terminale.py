"""Pulizia del testo prima di stamparlo nel terminale.

Tutto quello che arriva da fuori (configurazione, nomi di interfacce, e piu' avanti
i pacchetti di rete) passa da `pulisci()` prima di essere stampato: cosi' nessuno
puo' nascondere sequenze di escape che cambiano o falsano quello che vedi a schermo.
"""

from __future__ import annotations

import re

# caratteri di controllo C0, DEL e C1 (compreso ESC, che apre le sequenze ANSI), piu' i
# caratteri Unicode che girano o nascondono il testo (bidi, separatori di riga)
_CONTROLLO = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_MAX = 200


def pulisci(testo: object, massimo: int = _MAX) -> str:
    """Rende sicuro da stampare un valore non fidato: niente caratteri di controllo, lunghezza limitata."""
    s = _CONTROLLO.sub("?", str(testo))
    return s if len(s) <= massimo else s[: massimo - 1] + "…"
