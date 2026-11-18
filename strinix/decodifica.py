"""Decodifica dei pacchetti con Scapy: da byte grezzi a poche informazioni semplici.

Il resto di Strinix non vede mai oggetti Scapy, solo un `Pacchetto` con campi gia'
controllati. Cosi' i dati che arrivano dalla rete (non fidati) passano da un solo punto.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import logging
import re
from dataclasses import dataclass

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_DNS_PORTA = 53
_MAX_NOME = 253
_scapy = None


def scapy_disponibile() -> bool:
    return importlib.util.find_spec("scapy") is not None


def _carica_scapy():
    global _scapy
    if _scapy is None:
        logging.getLogger("scapy.runtime").setLevel(logging.ERROR)  # niente avvisi di Scapy a schermo
        from scapy.layers import dns, inet, inet6, l2
        _scapy = (l2.Ether, l2.ARP, inet.IP, inet.UDP, inet.TCP, inet6.IPv6, dns.DNS)
    return _scapy


@dataclass(frozen=True)
class Arp:
    operazione: int     # 1 = "chi ha questo IP?", 2 = "ce l'ho io"
    ip: str             # IP di chi parla
    mac: str            # MAC di chi parla
    ip_cercato: str


@dataclass(frozen=True)
class Pacchetto:
    mac_sorgente: str | None
    mac_destinazione: str | None
    ip_sorgente: str | None = None
    arp: Arp | None = None
    dns: str | None = None   # nome cercato, se e' una richiesta DNS


def _mac(valore: object) -> str | None:
    s = str(valore).lower()
    return s if _MAC.match(s) else None


def _ip(valore: object) -> str | None:
    try:
        return str(ipaddress.ip_address(str(valore)))
    except ValueError:
        return None


def _nome_dns(dns) -> str | None:
    if dns.qr != 0 or dns.opcode != 0 or not dns.qd:
        return None
    domanda = dns.qd[0] if isinstance(dns.qd, list) else dns.qd  # lista da Scapy 2.6, prima un pacchetto
    nome = domanda.qname
    if isinstance(nome, bytes):
        nome = nome.decode("ascii", "replace")
    nome = str(nome).rstrip(".")
    return nome[:_MAX_NOME] or None


def decodifica(dati: bytes) -> Pacchetto:
    """Lancia ValueError se il pacchetto non e' leggibile."""
    Ether, ARP, IP, UDP, TCP, IPv6, DNS = _carica_scapy()
    try:
        e = Ether(dati)
        arp = ip_src = nome = None
        if ARP in e:
            a = e[ARP]
            ip, mac, cercato = _ip(a.psrc), _mac(a.hwsrc), _ip(a.pdst)
            if ip and mac and cercato and a.op in (1, 2):
                arp = Arp(int(a.op), ip, mac, cercato)
        if IP in e:
            ip_src = _ip(e[IP].src)
        elif IPv6 in e:
            ip_src = _ip(e[IPv6].src)
        if DNS in e:
            trasporto = e[UDP] if UDP in e else (e[TCP] if TCP in e else None)
            if trasporto is not None and trasporto.dport == _DNS_PORTA:
                nome = _nome_dns(e[DNS])
        return Pacchetto(_mac(e.src), _mac(e.dst), ip_src, arp, nome)
    except Exception as exc:  # Scapy puo' sollevare di tutto su pacchetti costruiti apposta
        raise ValueError("pacchetto illeggibile") from exc
