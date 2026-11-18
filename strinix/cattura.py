"""Il custode della cattura: l'unico pezzo di Strinix che gira con privilegi.

Lo avvia `strinix ascolta` con sudo. Fa solo tre cose:
1. apre il socket di cattura sull'interfaccia richiesta (serve CAP_NET_RAW);
2. rinuncia per sempre a root, diventando l'utente "nobody";
3. copia i pacchetti, cosi' come sono, verso Strinix (stdout), senza leggerli.

Non decodifica niente: i dati non fidati li analizza Strinix, che non ha privilegi.
Usa solo la libreria standard e va eseguito con `python -I` (modalita' isolata),
cosi' non carica codice da altre cartelle o variabili d'ambiente.

Protocollo verso Strinix: la riga MAGIA, poi per ogni pacchetto
un'intestazione (ora: double, tipo: byte, lunghezza: uint32, big-endian) e i byte.
Si chiude quando Strinix chiude lo stdin.
"""

from __future__ import annotations

import ctypes
import os
import pwd
import re
import select
import socket
import struct
import sys
import time

MAGIA = b"STRX1\n"
INTESTAZIONE = struct.Struct(">dBI")
MAX_FRAME = 65535
ETH_P_ALL = 0x0003
PR_SET_NO_NEW_PRIVS = 38

ESCI_USO = 2          # argomenti sbagliati
ESCI_INTERFACCIA = 3  # interfaccia inesistente
ESCI_SOCKET = 4       # impossibile aprire la cattura
ESCI_PRIVILEGI = 5    # non avviato come root, o impossibile lasciare i privilegi

_NOME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")


def nome_valido(nome: str) -> bool:
    return bool(_NOME.match(nome)) and nome not in (".", "..")


def _scrivi_tutto(fd: int, dati: bytes) -> None:
    vista = memoryview(dati)
    while vista:
        vista = vista[os.write(fd, vista):]


def _lascia_privilegi() -> None:
    """Diventa "nobody": niente root, niente gruppi, niente capability, e niente modo di riprenderli."""
    utente = pwd.getpwnam("nobody")
    os.setgroups([])
    os.setgid(utente.pw_gid)
    os.setuid(utente.pw_uid)
    try:
        os.setuid(0)
    except PermissionError:
        pass
    else:
        raise OSError("root ancora recuperabile")
    if 0 in os.getresuid() or 0 in os.getresgid():
        raise OSError("privilegi ancora presenti")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError("no_new_privs non impostato")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not nome_valido(argv[0]):
        return ESCI_USO
    nome = argv[0]
    if os.geteuid() != 0:
        return ESCI_PRIVILEGI
    os.closerange(3, 4096)  # nessun file aperto ereditato oltre a stdin, stdout, stderr
    try:
        socket.if_nametoindex(nome)
    except OSError:
        return ESCI_INTERFACCIA
    try:
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
        s.bind((nome, 0))
    except OSError:
        return ESCI_SOCKET
    try:
        _lascia_privilegi()
    except (OSError, KeyError):
        s.close()
        return ESCI_PRIVILEGI
    os.chdir("/")
    try:
        _scrivi_tutto(1, MAGIA)
        while True:
            pronti, _, _ = select.select([s, 0], [], [])
            if 0 in pronti and not os.read(0, 4096):
                return 0  # Strinix ha chiuso: fine
            if s in pronti:
                dati, indirizzo = s.recvfrom(MAX_FRAME)
                tipo = indirizzo[2] if len(indirizzo) > 2 else 0
                _scrivi_tutto(1, INTESTAZIONE.pack(time.time(), tipo & 0xFF, len(dati)) + dati)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
