"""L'agente: ascolta la rete anche a cockpit chiuso e scrive tutto nell'archivio.

Oggi gira in primo piano (`strinix agente`), con il custode avviato da sudo come nel
cockpit; nella prossima tappa lo stesso codice girera' come servizio di sistema.
Uno solo alla volta: un secondo agente trova il lucchetto chiuso e si ferma.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from strinix.archivio import Archivio, ArchivioError

INTERVALLO = 2.0         # ogni quanto si salva nell'archivio
PULIZIA = 600.0          # ogni quanto si cancellano i dati scaduti


class Lucchetto:
    """Un file bloccato con flock: finche' l'agente gira, nessun altro agente puo' partire."""

    def __init__(self, percorso: Path) -> None:
        self.percorso = percorso
        self._fd: int | None = None

    def prendi(self) -> bool:
        self.percorso.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.percorso, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def lascia(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def esegui(sessione, archivio: Archivio, ferma: threading.Event, *, intervallo: float = INTERVALLO,
           al_salvataggio: Callable | None = None) -> str | None:
    """Ciclo dell'agente: salva ogni `intervallo` secondi finche' `ferma` non viene impostato.

    `sessione` deve essere gia' avviata. Restituisce la chiave dell'errore se la cattura si ferma da sola.
    """
    salvati = 0
    ultima_pulizia = 0.0
    errore = None
    try:
        archivio.pulisci_vecchi()
        while True:
            fine = ferma.wait(intervallo)
            ist = sessione.istantanea()
            prima = salvati
            salvati = archivio.salva(ist, salvati)
            if al_salvataggio is not None:
                al_salvataggio(ist, salvati - prima)
            if time.monotonic() - ultima_pulizia > PULIZIA:
                archivio.pulisci_vecchi()
                ultima_pulizia = time.monotonic()
            if not ist.attiva:
                errore = ist.errore[0] if ist.errore else "cattura.fermata"
                break
            if fine:
                break
    finally:
        sessione.ferma()
        try:
            archivio.ferma()
        except ArchivioError:
            pass
    return errore
