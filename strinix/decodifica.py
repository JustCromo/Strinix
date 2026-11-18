"""Decodifica dei pacchetti con Scapy: da byte grezzi a poche informazioni semplici.

Il resto di Strinix non vede mai oggetti Scapy, solo un `Pacchetto` con campi gia'
controllati. Cosi' i dati che arrivano dalla rete (non fidati) passano da un solo punto.

Regola d'oro: Strinix capisce *che* una password passa in chiaro, ma non la legge,
non la salva e non la mostra mai.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import logging
import re
from dataclasses import dataclass

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_NOME_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62})(?:\.[A-Za-z0-9_-]{1,63})*$")
_DNS_PORTA, _MDNS_PORTA, _HTTPS_PORTA = 53, 5353, 443
_MAX_NOME = 253
_MAX_CARICO = 4096        # quanti byte di contenuto guardiamo al massimo
_MAX_RISPOSTE = 20
_scapy = None

PORTE_HTTP = {80, 8080, 8000}
METODI_HTTP = (b"GET ", b"POST ", b"PUT ", b"HEAD ", b"DELETE ", b"OPTIONS ", b"PATCH ")
# protocollo -> (porte, comandi riconosciuti, comandi che portano una password)
IN_CHIARO = {
    "FTP": ({21}, (b"USER ", b"PASS ", b"RETR ", b"STOR ", b"LIST", b"CWD "), (b"PASS ",)),
    "POP3": ({110}, (b"USER ", b"PASS ", b"RETR ", b"STAT", b"LIST"), (b"PASS ",)),
    "SMTP": ({25, 587}, (b"EHLO ", b"HELO ", b"MAIL FROM", b"AUTH "), (b"AUTH PLAIN", b"AUTH LOGIN")),
}


def scapy_disponibile() -> bool:
    return importlib.util.find_spec("scapy") is not None


def _carica_scapy():
    global _scapy
    if _scapy is None:
        logging.getLogger("scapy.runtime").setLevel(logging.ERROR)  # niente avvisi di Scapy a schermo
        from scapy.layers import dhcp, dns, inet, inet6, l2
        _scapy = {"Ether": l2.Ether, "ARP": l2.ARP, "IP": inet.IP, "UDP": inet.UDP, "TCP": inet.TCP,
                  "ICMP": inet.ICMP, "IPv6": inet6.IPv6, "DNS": dns.DNS, "DNSRR": dns.DNSRR, "DHCP": dhcp.DHCP}
    return _scapy


@dataclass(frozen=True)
class Arp:
    operazione: int     # 1 = "chi ha questo IP?", 2 = "ce l'ho io"
    ip: str             # IP di chi parla
    mac: str            # MAC di chi parla
    ip_cercato: str


@dataclass(frozen=True)
class Chiaro:
    protocollo: str          # "HTTP", "FTP", "Telnet", "POP3", "IMAP", "SMTP"
    dove: str | None         # il sito (per HTTP), altrimenti None: si usa l'IP
    credenziali: bool        # sta passando una password (che Strinix NON legge)


@dataclass(frozen=True)
class Pacchetto:
    mac_sorgente: str | None
    mac_destinazione: str | None
    ip_sorgente: str | None = None
    arp: Arp | None = None
    dns: str | None = None                       # nome cercato, se e' una richiesta DNS
    ip_destinazione: str | None = None
    protocollo: str | None = None                # "tcp", "udp", "icmp"
    porta_sorgente: int | None = None
    porta_destinazione: int | None = None
    nuova_connessione: bool = False              # TCP: il primo SYN; UDP: ogni pacchetto
    lunghezza: int = 0
    sni: str | None = None                       # il sito chiesto in una connessione HTTPS
    risposte_dns: tuple[tuple[str, str], ...] = ()   # (nome, ip) dalle risposte DNS
    nome_host: str | None = None                 # il nome che il dispositivo dichiara (DHCP, mDNS)
    chiaro: Chiaro | None = None
    dhcp_tipo: int | None = None                 # 1 discover, 2 offer, 3 request, 5 ack...
    dhcp_server: str | None = None               # chi dice di essere il server DHCP
    dhcp_router: str | None = None               # il router che quel server sta proponendo


def _mac(valore: object) -> str | None:
    s = str(valore).lower()
    return s if _MAC.match(s) else None


def _ip(valore: object) -> str | None:
    try:
        return str(ipaddress.ip_address(str(valore)))
    except ValueError:
        return None


def nome_valido(valore: object, massimo: int = _MAX_NOME) -> str | None:
    """Un nome di host/dominio con solo caratteri ammessi, oppure None."""
    if isinstance(valore, bytes):
        try:
            valore = valore.decode("ascii")
        except UnicodeDecodeError:
            return None
    if not isinstance(valore, str):
        return None
    nome = valore.strip().rstrip(".")
    if not nome or len(nome) > massimo or not _NOME_HOST.match(nome):
        return None
    return nome


def _lista(campo, tipo) -> list:
    """Le risposte DNS: una lista da Scapy 2.6, prima una catena di pacchetti."""
    if isinstance(campo, list):
        return campo[:_MAX_RISPOSTE]
    fuori = []
    while isinstance(campo, tipo) and len(fuori) < _MAX_RISPOSTE:
        fuori.append(campo)
        campo = campo.payload
    return fuori


def _nome_dns(dns) -> str | None:
    if dns.qr != 0 or dns.opcode != 0 or not dns.qd:
        return None
    domanda = dns.qd[0] if isinstance(dns.qd, list) else dns.qd  # lista da Scapy 2.6, prima un pacchetto
    nome = domanda.qname
    if isinstance(nome, bytes):
        nome = nome.decode("ascii", "replace")
    nome = str(nome).rstrip(".")
    return nome[:_MAX_NOME] or None


def _risposte(dns, DNSRR) -> list[tuple[str, str]]:
    fuori = []
    for r in _lista(dns.an, DNSRR):
        if getattr(r, "type", None) in (1, 28):          # A e AAAA
            nome, ip = nome_valido(r.rrname), _ip(r.rdata)
            if nome and ip:
                fuori.append((nome, ip))
    return fuori


def sni_da_client_hello(dati: bytes) -> str | None:
    """Il nome del sito dal primo messaggio TLS (ClientHello). Ogni lunghezza viene controllata."""
    try:
        if len(dati) < 43 or dati[0] != 0x16 or dati[1] != 0x03 or dati[5] != 0x01:
            return None
        p = 5 + 4 + 2 + 32                               # record, intestazione handshake, versione, random
        p += 1 + dati[p]                                 # session id
        p += 2 + int.from_bytes(dati[p:p + 2], "big")    # cipher suites
        p += 1 + dati[p]                                 # compressione
        fine = min(len(dati), p + 2 + int.from_bytes(dati[p:p + 2], "big"))
        p += 2
        while p + 4 <= fine:
            tipo = int.from_bytes(dati[p:p + 2], "big")
            lung = int.from_bytes(dati[p + 2:p + 4], "big")
            p += 4
            if p + lung > fine:
                return None
            if tipo == 0 and lung >= 5 and dati[p + 2] == 0:   # server_name, tipo host_name
                n = int.from_bytes(dati[p + 3:p + 5], "big")
                if 5 + n <= lung:
                    return nome_valido(bytes(dati[p + 5:p + 5 + n]))
                return None
            p += lung
    except IndexError:
        return None
    return None


def _http(carico: bytes) -> Chiaro | None:
    if not carico.startswith(METODI_HTTP):
        return None
    testa, _, corpo = carico.partition(b"\r\n\r\n")
    host = None
    for riga in testa.split(b"\r\n")[1:50]:
        nome, _, valore = riga.partition(b":")
        if nome.strip().lower() == b"host":
            host = nome_valido(valore.strip().split(b":")[0])
            break
    minuscolo = testa.lower()
    credenziali = b"\r\nauthorization: basic " in minuscolo or (
        carico.startswith(b"POST ") and re.search(rb"(?:^|&)(?:pass|passwd|password|pwd)=", corpo.lower()) is not None)
    return Chiaro("HTTP", host, credenziali)


def in_chiaro(porta: int, carico: bytes) -> Chiaro | None:
    """Riconosce i protocolli che viaggiano senza cifratura, dal lato di chi si collega."""
    if not carico:
        return None
    if porta in PORTE_HTTP:
        return _http(carico)
    if porta == 23:
        return Chiaro("Telnet", None, False)
    maiuscolo = carico[:200].upper()
    if porta == 143:
        parti = maiuscolo.split(b" ", 2)
        if len(parti) >= 2 and parti[1] in (b"LOGIN", b"AUTHENTICATE", b"SELECT", b"FETCH", b"LIST"):
            return Chiaro("IMAP", None, parti[1] in (b"LOGIN", b"AUTHENTICATE"))
        return None
    for nome, (porte, comandi, segreti) in IN_CHIARO.items():
        if porta in porte and maiuscolo.startswith(comandi):
            return Chiaro(nome, None, maiuscolo.startswith(segreti))
    return None


def decodifica(dati: bytes) -> Pacchetto:
    """Lancia ValueError se il pacchetto non e' leggibile."""
    s = _carica_scapy()
    try:
        e = s["Ether"](dati)
        campi: dict = {"lunghezza": len(dati)}
        if s["ARP"] in e:
            a = e[s["ARP"]]
            ip, mac, cercato = _ip(a.psrc), _mac(a.hwsrc), _ip(a.pdst)
            if ip and mac and cercato and a.op in (1, 2):
                campi["arp"] = Arp(int(a.op), ip, mac, cercato)
        if s["IP"] in e:
            campi["ip_sorgente"], campi["ip_destinazione"] = _ip(e[s["IP"]].src), _ip(e[s["IP"]].dst)
        elif s["IPv6"] in e:
            campi["ip_sorgente"], campi["ip_destinazione"] = _ip(e[s["IPv6"]].src), _ip(e[s["IPv6"]].dst)
        trasporto = None
        if s["TCP"] in e:
            trasporto = e[s["TCP"]]
            campi["protocollo"] = "tcp"
            flag = int(trasporto.flags)
            campi["nuova_connessione"] = bool(flag & 0x02) and not flag & 0x10      # SYN senza ACK
        elif s["UDP"] in e:
            trasporto = e[s["UDP"]]
            campi["protocollo"] = "udp"
            campi["nuova_connessione"] = True
        elif s["ICMP"] in e:
            campi["protocollo"] = "icmp"
        if trasporto is not None:
            campi["porta_sorgente"], campi["porta_destinazione"] = int(trasporto.sport), int(trasporto.dport)
            carico = bytes(trasporto.payload)[:_MAX_CARICO]
            porta = campi["porta_destinazione"]
            if campi["protocollo"] == "tcp":
                if porta == _HTTPS_PORTA:
                    campi["sni"] = sni_da_client_hello(carico)
                campi["chiaro"] = in_chiaro(porta, carico)
        if s["DNS"] in e and trasporto is not None:
            d = e[s["DNS"]]
            if campi["porta_destinazione"] == _DNS_PORTA:
                campi["dns"] = _nome_dns(d)
            elif campi["porta_sorgente"] == _DNS_PORTA and d.qr == 1:
                campi["risposte_dns"] = tuple(_risposte(d, s["DNSRR"]))
            elif campi["porta_sorgente"] == _MDNS_PORTA and d.qr == 1:
                for nome, _ in _risposte(d, s["DNSRR"]):
                    if nome.lower().endswith(".local"):
                        campi["nome_host"] = nome_valido(nome[:-6], 63)
                        break
        if s["DHCP"] in e:
            for opzione in e[s["DHCP"]].options[:64]:
                if not (isinstance(opzione, tuple) and len(opzione) >= 2):
                    continue
                nome, valore = opzione[0], opzione[1]
                if nome == "hostname" and "nome_host" not in campi:
                    campi["nome_host"] = nome_valido(valore, 63)
                elif nome == "message-type" and isinstance(valore, int) and 1 <= valore <= 18:
                    campi["dhcp_tipo"] = valore
                elif nome == "server_id":
                    campi["dhcp_server"] = _ip(valore)
                elif nome == "router":
                    campi["dhcp_router"] = _ip(valore)
        return Pacchetto(_mac(e.src), _mac(e.dst), **campi)
    except Exception as exc:  # Scapy puo' sollevare di tutto su pacchetti costruiti apposta
        raise ValueError("pacchetto illeggibile") from exc
