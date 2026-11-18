"""Lettura e scrittura prudente dei file di Strinix (configurazione, cronologia, stato).

Regole: niente collegamenti simbolici, solo file normali, solo file tuoi e non
modificabili da altri, dimensione massima. I file nuovi nascono leggibili solo da te.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


class FileNonSicuro(Exception):
    """`motivo` e' uno tra: link, illeggibile, non_file, proprietario, permessi, troppo_grande, esiste."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


def leggi(percorso: Path, massimo: int) -> bytes | None:
    """Contenuto del file, oppure None se non esiste. Lancia FileNonSicuro se qualcosa non va."""
    try:
        # O_NONBLOCK: una FIFO al posto del file non deve bloccare Strinix
        fd = os.open(percorso, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FileNonSicuro("link" if exc.errno == errno.ELOOP else "illeggibile") from None
    try:
        st = os.fstat(fd)  # controlli sul file gia' aperto: nessuno puo' scambiarlo nel frattempo
        if not stat.S_ISREG(st.st_mode):
            raise FileNonSicuro("non_file")
        if st.st_uid != os.geteuid():
            raise FileNonSicuro("proprietario")
        if st.st_mode & 0o022:
            raise FileNonSicuro("permessi")
        dati = os.read(fd, massimo + 1)
    finally:
        os.close(fd)
    if len(dati) > massimo:
        raise FileNonSicuro("troppo_grande")
    return dati


def scrivi(percorso: Path, testo: str, *, sovrascrivi: bool) -> None:
    """Scrive un file privato (600) in una cartella privata (700), senza mai seguire collegamenti."""
    percorso.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flag = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    flag |= os.O_TRUNC if sovrascrivi else os.O_EXCL
    try:
        fd = os.open(percorso, flag, 0o600)
    except FileExistsError:
        raise FileNonSicuro("esiste") from None
    except OSError as exc:
        raise FileNonSicuro("link" if exc.errno == errno.ELOOP else "illeggibile") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        if os.fstat(f.fileno()).st_uid != os.geteuid():
            raise FileNonSicuro("proprietario")
        f.write(testo)


def cartella_stato() -> Path:
    """~/.local/state/strinix (o $XDG_STATE_HOME/strinix se e' un percorso assoluto)."""
    base = os.environ.get("XDG_STATE_HOME", "")
    radice = Path(base) if base and os.path.isabs(base) else Path.home() / ".local" / "state"
    return radice / "strinix"
