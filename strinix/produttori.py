"""Chi ha fabbricato un dispositivo, dai primi 3 byte del MAC (l'OUI, assegnato dall'IEEE).

Usa l'elenco dei produttori che Scapy si porta dietro (lo stesso di Wireshark), quindi
funziona senza internet. I MAC "casuali" degli smartphone non hanno un produttore.
"""

from __future__ import annotations

import re

from strinix.terminale import pulisci

_SUFFISSI = {"inc", "inc.", "corp", "corp.", "corporation", "corporate", "co", "co.", "co.,", "ltd", "ltd.",
             "limited", "llc", "gmbh", "s.p.a.", "spa", "s.a.", "sa", "ag", "pte", "pte.", "plc", "bv", "b.v.",
             "oy", "ab", "kg", "srl", "s.r.l.", "company", "group", "holdings", "international"}
_MAX = 24


def accorcia(nome: str) -> str:
    """"Intel Corporate" -> "Intel", "Cloud Network Technology Singapore Pte. Ltd." -> "Cloud Network Technology"."""
    parole = []
    for parola in re.split(r"[\s,]+", nome.strip()):
        if not parola or parola.lower() in _SUFFISSI:
            break
        parole.append(parola)
        if len(parole) == 3:
            break
    while len(" ".join(parole)) > _MAX and len(parole) > 1:   # meglio una parola in meno che una a meta'
        parole.pop()
    corto = " ".join(parole) or nome.strip()
    return pulisci(corto, _MAX)


def produttore(mac: str) -> str | None:
    if int(mac[:2], 16) & 0x02:     # MAC inventato: nessun produttore
        return None
    try:
        from scapy.config import conf
        _, lungo = conf.manufdb.lookup(mac)
    except Exception:  # Scapy vecchio o elenco mancante: semplicemente non lo sappiamo
        return None
    if not isinstance(lungo, str) or not lungo or lungo.lower() == mac.lower():
        return None
    return accorcia(lungo) or None
