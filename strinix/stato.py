"""La memoria a breve termine di Strinix: chi ha visto e cosa e' cambiato.

Riceve un `Pacchetto` alla volta e restituisce gli `Evento` da raccontare.
Ha limiti fissi di dimensione: chi inonda la rete di indirizzi falsi non puo'
riempire la RAM, e Strinix lo segnala una volta sola.
"""

from __future__ import annotations

import ipaddress
from collections import OrderedDict
from dataclasses import dataclass, field

from strinix.decodifica import Pacchetto

MAX_DISPOSITIVI = 1024
MAX_DNS = 512
DNS_SILENZIO = 60.0  # secondi: la stessa richiesta dallo stesso dispositivo non si ripete prima


@dataclass(frozen=True)
class Evento:
    ora: float
    livello: str    # "info" o "avviso"
    chiave: str
    valori: dict = field(default_factory=dict)


def _ip_locale(ip: str | None) -> bool:
    if ip is None:
        return False
    a = ipaddress.ip_address(ip)
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local and str(a) != "0.0.0.0"


def _unicast(mac: str | None) -> bool:
    return mac is not None and not int(mac[:2], 16) & 1 and mac != "00:00:00:00:00:00"


class Memoria:
    def __init__(self, mio_mac: str | None = None, router_mac: str | None = None,
                 max_dispositivi: int = MAX_DISPOSITIVI, max_dns: int = MAX_DNS) -> None:
        self.mio_mac, self.router_mac = mio_mac, router_mac
        self.max_dispositivi, self.max_dns = max_dispositivi, max_dns
        self.dispositivi: dict[str, float] = {}       # MAC -> prima volta visto
        self.arp: dict[str, str] = {}                 # IP -> MAC
        self.dns: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.piena = False

    def _etichetta(self, mac: str) -> str:
        if mac == self.mio_mac:
            return "questo_computer"
        if mac == self.router_mac:
            return "router"
        return "nessuna"

    def _segnala_piena(self, ora: float, eventi: list[Evento]) -> None:
        if not self.piena:
            self.piena = True
            eventi.append(Evento(ora, "avviso", "evento.memoria_piena", {"massimo": self.max_dispositivi}))

    def osserva(self, p: Pacchetto, ora: float) -> list[Evento]:
        eventi: list[Evento] = []
        ip_noto = None

        if p.arp and _ip_locale(p.arp.ip) and _unicast(p.arp.mac):
            prima = self.arp.get(p.arp.ip)
            if prima is None:
                if len(self.arp) < self.max_dispositivi:
                    self.arp[p.arp.ip] = p.arp.mac
                else:
                    self._segnala_piena(ora, eventi)
            elif prima != p.arp.mac:
                self.arp[p.arp.ip] = p.arp.mac
                eventi.append(Evento(ora, "avviso", "evento.arp_cambio",
                                     {"ip": p.arp.ip, "prima": prima, "ora": p.arp.mac}))
            if p.arp.mac == p.mac_sorgente:
                ip_noto = p.arp.ip
        elif _ip_locale(p.ip_sorgente):
            ip_noto = p.ip_sorgente

        mac = p.mac_sorgente
        if _unicast(mac) and mac not in self.dispositivi:
            if len(self.dispositivi) < self.max_dispositivi:
                self.dispositivi[mac] = ora
                eventi.append(Evento(ora, "info", "evento.nuovo_dispositivo",
                                     {"mac": mac, "ip": ip_noto or "-", "etichetta": self._etichetta(mac)}))
            else:
                self._segnala_piena(ora, eventi)

        if p.dns and p.ip_sorgente:
            chiave = (p.ip_sorgente, p.dns.lower())
            ultima = self.dns.get(chiave)
            if ultima is None or ora - ultima >= DNS_SILENZIO:
                eventi.append(Evento(ora, "info", "evento.dns", {"ip": p.ip_sorgente, "nome": p.dns}))
            self.dns[chiave] = ora
            self.dns.move_to_end(chiave)
            while len(self.dns) > self.max_dns:
                self.dns.popitem(last=False)
        return eventi
