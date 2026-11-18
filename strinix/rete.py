"""Interfacce di rete e strada verso internet, lette dal kernel senza permessi speciali.

- /sys/class/net/<nome>/ : una cartella per ogni interfaccia (stato, MAC, tipo)
- /proc/net/route        : tabella di routing; la riga con destinazione 0.0.0.0 e' la
                           "strada predefinita", e il suo gateway e' il router di casa
"""

from __future__ import annotations

import fcntl
import re
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from strinix.config import nome_interfaccia_valido

SYS_NET = Path("/sys/class/net")
PROC_ROUTE = Path("/proc/net/route")
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_SIOCGIFADDR = 0x8915  # ioctl che restituisce l'indirizzo IPv4 di un'interfaccia
_RTF_UP, _RTF_GATEWAY = 0x1, 0x2


@dataclass(frozen=True)
class Interfaccia:
    nome: str
    tipo: str          # "wifi", "cablata", "loopback", "virtuale"
    stato: str         # "attiva", "spenta", "sconosciuto"
    mac: str | None
    ipv4: str | None


@dataclass(frozen=True)
class Strada:
    interfaccia: str
    gateway: str


def _leggi(percorso: Path, massimo: int = 256) -> str:
    try:
        with open(percorso, encoding="ascii", errors="replace") as f:
            return f.read(massimo).strip()
    except OSError:
        return ""


def ipv4_di(nome: str) -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            r = fcntl.ioctl(s.fileno(), _SIOCGIFADDR, struct.pack("256s", nome.encode("ascii")[:15]))
        except OSError:
            return None
    return socket.inet_ntoa(r[20:24])


def _tipo(cartella: Path, nome: str) -> str:
    if nome == "lo" or _leggi(cartella / "type") == "772":
        return "loopback"
    if (cartella / "wireless").exists() or (cartella / "phy80211").exists():
        return "wifi"
    if not (cartella / "device").exists():  # le interfacce virtuali non hanno hardware dietro
        return "virtuale"
    return "cablata"


def _stato(cartella: Path) -> str:
    return {"up": "attiva", "down": "spenta", "dormant": "spenta"}.get(_leggi(cartella / "operstate"), "sconosciuto")


def elenca(sys_net: Path = SYS_NET, leggi_ipv4: Callable[[str], str | None] = ipv4_di) -> list[Interfaccia]:
    risultato = []
    try:
        voci = sorted(p for p in sys_net.iterdir())
    except OSError:
        return []
    for cartella in voci:
        nome = cartella.name
        if not nome_interfaccia_valido(nome):  # nomi strani: li ignoriamo invece di stamparli
            continue
        mac = _leggi(cartella / "address").lower()
        risultato.append(Interfaccia(
            nome=nome,
            tipo=_tipo(cartella, nome),
            stato=_stato(cartella),
            mac=mac if _MAC.match(mac) and mac != "00:00:00:00:00:00" else None,
            ipv4=leggi_ipv4(nome),
        ))
    return risultato


def strada_predefinita(route: Path = PROC_ROUTE) -> Strada | None:
    """La strada verso internet con la metrica piu' bassa, oppure None."""
    migliore: tuple[int, Strada] | None = None
    try:
        righe = route.read_text(encoding="ascii", errors="replace").splitlines()[1:65]
    except OSError:
        return None
    for riga in righe:
        campi = riga.split()
        if len(campi) < 7:
            continue
        nome, destinazione, gateway, flag, metrica = campi[0], campi[1], campi[2], campi[3], campi[6]
        try:
            if destinazione != "00000000" or not nome_interfaccia_valido(nome):
                continue
            f = int(flag, 16)
            if not (f & _RTF_UP and f & _RTF_GATEWAY):
                continue
            ip = socket.inet_ntoa(struct.pack("<I", int(gateway, 16)))
            m = int(metrica)
        except (ValueError, struct.error, OverflowError):
            continue
        if migliore is None or m < migliore[0]:
            migliore = (m, Strada(interfaccia=nome, gateway=ip))
    return migliore[1] if migliore else None


def scegli(richiesta: str, interfacce: list[Interfaccia], strada: Strada | None) -> Interfaccia | None:
    """Interfaccia da usare: quella scelta in configurazione, altrimenti quella verso internet."""
    per_nome = {i.nome: i for i in interfacce}
    if richiesta != "auto":
        return per_nome.get(richiesta)
    if strada and strada.interfaccia in per_nome:
        return per_nome[strada.interfaccia]
    attive = [i for i in interfacce if i.stato == "attiva" and i.tipo in ("wifi", "cablata")]
    return attive[0] if attive else None
