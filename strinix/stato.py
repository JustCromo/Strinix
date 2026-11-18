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


@dataclass
class Dispositivo:
    mac: str
    primo: float          # prima volta visto
    ultimo: float         # ultima volta visto
    pacchetti: int = 0
    ip: str | None = None

    @property
    def mac_casuale(self) -> bool:
        """Bit "amministrato localmente": MAC inventato, tipico degli smartphone con Wi-Fi privato."""
        return bool(int(self.mac[:2], 16) & 0x02)


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
    def __init__(self, mio_mac: str | None = None, router_mac: str | None = None, mio_ip: str | None = None,
                 max_dispositivi: int = MAX_DISPOSITIVI, max_dns: int = MAX_DNS) -> None:
        self.mio_mac, self.router_mac, self.mio_ip = mio_mac, router_mac, mio_ip
        self.dns_in_chiaro_mio = 0    # richieste DNS non cifrate partite da questo computer
        self.max_dispositivi, self.max_dns = max_dispositivi, max_dns
        self.dispositivi: dict[str, Dispositivo] = {}  # MAC -> scheda del dispositivo
        self.arp: dict[str, str] = {}                 # IP -> MAC
        self.dns: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.piena = False

    def etichetta(self, mac: str) -> str:
        if mac == self.mio_mac:
            return "questo_computer"
        if mac == self.router_mac:
            return "router"
        if int(mac[:2], 16) & 0x02:
            return "mac_casuale"
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
        if _unicast(mac):
            scheda = self.dispositivi.get(mac)
            if scheda is not None:
                scheda.ultimo = ora
                scheda.pacchetti += 1
                if ip_noto:
                    scheda.ip = ip_noto
            elif len(self.dispositivi) < self.max_dispositivi:
                if ip_noto is None and mac == self.mio_mac:
                    ip_noto = self.mio_ip
                self.dispositivi[mac] = Dispositivo(mac, ora, ora, 1, ip_noto)
                eventi.append(Evento(ora, "info", "evento.nuovo_dispositivo",
                                     {"mac": mac, "ip": ip_noto or "-", "etichetta": self.etichetta(mac)}))
            else:
                self._segnala_piena(ora, eventi)

        if p.dns and p.ip_sorgente:
            chiave = (p.ip_sorgente, p.dns.lower())
            ultima = self.dns.get(chiave)
            if p.mac_sorgente == self.mio_mac:
                self.dns_in_chiaro_mio += 1
            if ultima is None or ora - ultima >= DNS_SILENZIO:
                eventi.append(Evento(ora, "info", "evento.dns", {"ip": p.ip_sorgente, "nome": p.dns}))
            self.dns[chiave] = ora
            self.dns.move_to_end(chiave)
            while len(self.dns) > self.max_dns:
                self.dns.popitem(last=False)
        return eventi
