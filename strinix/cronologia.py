"""Cronologia dei comandi scritti nel cockpit e nella console (~/.local/state/strinix/cronologia).

E' solo una comodita': se il file manca, e' rovinato o non e' sicuro, si riparte da vuota.
"""

from __future__ import annotations

from pathlib import Path

from strinix import file_sicuri
from strinix.terminale import pulisci

MAX_RIGHE = 500
MAX_BYTE = 64 * 1024
MAX_RIGA = 200


def percorso() -> Path:
    return file_sicuri.cartella_stato() / "cronologia"


def carica(p: Path | None = None) -> list[str]:
    try:
        dati = file_sicuri.leggi(p or percorso(), MAX_BYTE)
    except file_sicuri.FileNonSicuro:
        return []
    if not dati:
        return []
    righe = [pulisci(r, MAX_RIGA) for r in dati.decode("utf-8", "replace").splitlines()]
    return [r for r in righe if r.strip()][-MAX_RIGHE:]


def aggiungi(storia: list[str], riga: str) -> list[str]:
    riga = pulisci(riga.strip(), MAX_RIGA)
    if not riga or (storia and storia[-1] == riga):
        return storia
    return (storia + [riga])[-MAX_RIGHE:]


def salva(storia: list[str], p: Path | None = None) -> bool:
    try:
        file_sicuri.scrivi(p or percorso(), "".join(r + "\n" for r in storia[-MAX_RIGHE:]), sovrascrivi=True)
    except (file_sicuri.FileNonSicuro, OSError):
        return False
    return True
