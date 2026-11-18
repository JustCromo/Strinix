"""Lato Strinix della cattura: avvia il custode con sudo e legge i pacchetti che manda.

Strinix non ha privilegi: riceve solo i byte dal custode (vedi cattura.py) e li
controlla prima di usarli, come qualsiasi altro input non fidato.
"""

from __future__ import annotations

import math
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from strinix import cattura
from strinix.config import nome_interfaccia_valido

CUSTODE = Path(cattura.__file__).resolve()

ERRORI = {
    1: "cattura.sudo",
    cattura.ESCI_USO: "cattura.uso",
    cattura.ESCI_INTERFACCIA: "cattura.interfaccia",
    cattura.ESCI_SOCKET: "cattura.socket",
    cattura.ESCI_PRIVILEGI: "cattura.privilegi",
}


class CatturaError(Exception):
    def __init__(self, chiave: str, /, **valori: object) -> None:
        super().__init__(chiave)
        self.chiave = chiave
        self.valori = valori


@dataclass(frozen=True)
class Frame:
    ora: float
    tipo: int     # 0 per me, 1 broadcast, 2 multicast, 3 per altri, 4 in uscita
    dati: bytes


def comando(interfaccia: str, python: str = sys.executable) -> list[str]:
    return ["sudo", "--", python, "-I", str(CUSTODE), interfaccia]


def _leggi(stream: BinaryIO, n: int) -> bytes:
    dati = b""
    while len(dati) < n:
        pezzo = stream.read(n - len(dati))
        if not pezzo:
            break
        dati += pezzo
    return dati


def leggi_frame(stream: BinaryIO) -> Iterator[Frame]:
    """Legge il flusso del custode controllando ogni campo. Finisce quando il flusso finisce."""
    magia = _leggi(stream, len(cattura.MAGIA))
    if not magia:
        return
    if magia != cattura.MAGIA:
        raise CatturaError("cattura.protocollo")
    while True:
        testa = _leggi(stream, cattura.INTESTAZIONE.size)
        if not testa:
            return
        if len(testa) != cattura.INTESTAZIONE.size:
            raise CatturaError("cattura.protocollo")
        ora, tipo, n = cattura.INTESTAZIONE.unpack(testa)
        if n > cattura.MAX_FRAME or tipo > 7 or not math.isfinite(ora):
            raise CatturaError("cattura.protocollo")
        dati = _leggi(stream, n)
        if len(dati) != n:
            raise CatturaError("cattura.protocollo")
        yield Frame(ora, tipo, dati)


class Cattura:
    """Uso: `with Cattura("wlan0") as c: for f in c.frame(): ...`"""

    def __init__(self, interfaccia: str, cmd: list[str] | None = None) -> None:
        if not nome_interfaccia_valido(interfaccia):
            raise CatturaError("config.interfaccia_non_valida")
        self.cmd = cmd or comando(interfaccia)
        self.proc: subprocess.Popen | None = None
        self.avviata = False

    def __enter__(self) -> Cattura:
        try:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True)
        except OSError:
            raise CatturaError("cattura.sudo_manca") from None
        return self

    def frame(self) -> Iterator[Frame]:
        assert self.proc is not None and self.proc.stdout is not None
        for f in leggi_frame(self.proc.stdout):
            self.avviata = True
            yield f
        codice = self.proc.wait()
        if not self.avviata:
            raise CatturaError(ERRORI.get(codice, "cattura.fermata"), codice=codice)
        if codice != 0:
            raise CatturaError("cattura.fermata", codice=codice)

    def __exit__(self, *exc: object) -> None:
        if self.proc is None:
            return
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream:
                    stream.close()  # chiudere lo stdin dice al custode di fermarsi
            except OSError:
                pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc.wait()
