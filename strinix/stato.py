"""La memoria a breve termine di Strinix: chi c'e', cosa fa, cosa e' cambiato.

Riceve un `Pacchetto` alla volta e restituisce gli `Evento` da raccontare.
Ogni elenco ha un limite fisso: chi inonda la rete di indirizzi o connessioni
false non puo' riempire la RAM, e Strinix lo segnala una volta sola.
"""

from __future__ import annotations

import ipaddress
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from strinix.decodifica import Pacchetto
from strinix.produttori import produttore as _produttore
from strinix.rilevatori import Rilevatori

MAX_DISPOSITIVI = 1024
MAX_DNS = 512
MAX_NOMI_IP = 4096
MAX_CONNESSIONI = 2048
MAX_CHIARO = 256
MAX_DESTINAZIONI = 16     # per ogni dispositivo
DNS_SILENZIO = 60.0       # la stessa richiesta DNS dallo stesso dispositivo non si ripete prima di 60 s
CONNESSIONE_SILENZIO = 300.0
CHIARO_SILENZIO = 300.0
ONLINE = 300.0            # visto negli ultimi 5 minuti = online
RITORNO = 3600.0          # se un dispositivo noto ricompare dopo un'ora, Strinix lo dice
# controlli di connessione dei sistemi operativi e dei browser: viaggiano in HTTP apposta
# (servono a scoprire le pagine di login dei wifi) e non portano nulla di personale
CONTROLLI_CONNESSIONE = frozenset({
    "ping.archlinux.org", "nmcheck.gnome.org", "network-test.debian.org", "connectivity-check.ubuntu.com",
    "detectportal.firefox.com", "connectivitycheck.gstatic.com", "connectivitycheck.android.com",
    "clients3.google.com", "captive.apple.com", "www.msftconnecttest.com", "www.msftncsi.com",
})

SERVIZI = {
    ("tcp", 443): "HTTPS", ("udp", 443): "HTTPS (QUIC)", ("tcp", 80): "HTTP", ("tcp", 8080): "HTTP",
    ("udp", 53): "DNS", ("tcp", 53): "DNS", ("tcp", 853): "DNS cifrato", ("udp", 853): "DNS cifrato",
    ("tcp", 22): "SSH", ("tcp", 21): "FTP", ("tcp", 23): "Telnet", ("udp", 123): "ora (NTP)",
    ("tcp", 25): "posta (SMTP)", ("tcp", 587): "posta (SMTP)", ("tcp", 465): "posta (SMTPS)",
    ("tcp", 110): "posta (POP3)", ("tcp", 995): "posta (POP3S)", ("tcp", 143): "posta (IMAP)",
    ("tcp", 993): "posta (IMAPS)", ("tcp", 445): "condivisione file (SMB)", ("tcp", 3389): "desktop remoto",
    ("udp", 3478): "chiamate (STUN)", ("udp", 19302): "chiamate (STUN)", ("udp", 51820): "VPN (WireGuard)",
    ("udp", 1194): "VPN (OpenVPN)", ("tcp", 1194): "VPN (OpenVPN)", ("tcp", 5222): "chat (XMPP)",
    ("tcp", 8883): "domotica (MQTT)", ("tcp", 1883): "domotica (MQTT)", ("udp", 67): "DHCP", ("udp", 68): "DHCP",
}


@dataclass
class Dispositivo:
    mac: str
    primo: float          # prima volta visto
    ultimo: float         # ultima volta visto
    pacchetti: int = 0
    ip: str | None = None
    produttore: str | None = None
    nome: str | None = None                  # il nome che dichiara in rete (es. "iPhone-di-Ivan")
    byte_inviati: int = 0
    byte_ricevuti: int = 0
    destinazioni: dict[str, int] = field(default_factory=dict)   # sito/servizio -> quante connessioni

    @property
    def mac_casuale(self) -> bool:
        """Bit "amministrato localmente": MAC inventato, tipico degli smartphone con Wi-Fi privato."""
        return bool(int(self.mac[:2], 16) & 0x02)

    def online(self, adesso: float) -> bool:
        return adesso - self.ultimo < ONLINE


@dataclass
class InChiaro:
    """Un uso di protocollo non cifrato: chi, con cosa, verso dove. Mai il contenuto."""
    primo: float
    ultimo: float
    chi: str
    protocollo: str
    dove: str
    credenziali: bool = False
    volte: int = 1


@dataclass(frozen=True)
class Evento:
    ora: float
    livello: str    # "info", "avviso" o "grave"
    chiave: str
    valori: dict = field(default_factory=dict)


def _ip_locale(ip: str | None) -> bool:
    if ip is None:
        return False
    a = ipaddress.ip_address(ip)
    return a.is_private and not a.is_loopback and not a.is_multicast and not a.is_unspecified and (
        a.version == 6 or not a.is_link_local)


def _ipv4_locale(ip: str | None) -> bool:
    return _ip_locale(ip) and ipaddress.ip_address(ip).version == 4


def _destinazione_valida(ip: str | None) -> bool:
    if ip is None:
        return False
    a = ipaddress.ip_address(ip)
    return not (a.is_multicast or a.is_unspecified or a.is_loopback or str(a) == "255.255.255.255")


def _unicast(mac: str | None) -> bool:
    return mac is not None and not int(mac[:2], 16) & 1 and mac != "00:00:00:00:00:00"


def _ricorda(lru: OrderedDict, chiave, valore, massimo: int) -> None:
    lru[chiave] = valore
    lru.move_to_end(chiave)
    while len(lru) > massimo:
        lru.popitem(last=False)


class Memoria:
    def __init__(self, mio_mac: str | None = None, router_mac: str | None = None, mio_ip: str | None = None,
                 max_dispositivi: int = MAX_DISPOSITIVI, max_dns: int = MAX_DNS,
                 produttore: Callable[[str], str | None] = _produttore, router_ip: str | None = None) -> None:
        self.mio_mac, self.router_mac, self.mio_ip, self.router_ip = mio_mac, router_mac, mio_ip, router_ip
        self.max_dispositivi, self.max_dns = max_dispositivi, max_dns
        self._produttore = produttore
        self.dispositivi: dict[str, Dispositivo] = {}  # MAC -> scheda del dispositivo
        self.arp: dict[str, str] = {}                 # IP -> MAC
        self.dns: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.nomi_ip: OrderedDict[str, str] = OrderedDict()           # IP remoto -> nome (DNS o HTTPS)
        self.connessioni: OrderedDict[tuple, float] = OrderedDict()
        self.chiaro: OrderedDict[tuple, InChiaro] = OrderedDict()
        self.dns_in_chiaro_mio = 0    # richieste DNS non cifrate partite da questo computer
        self.piena = False
        self.rilevatori = Rilevatori(self)
        if router_ip and router_mac:
            # il MAC del router lo conosciamo gia' (dal kernel): chi prova a prenderne il posto si vede subito
            self.arp[router_ip] = router_mac

    def carica(self, schede: list[Dispositivo]) -> None:
        """Ricorda i dispositivi gia' visti (dall'archivio): non sono "nuovi", e se tornano si dice."""
        for d in sorted(schede, key=lambda d: d.ultimo)[-self.max_dispositivi:]:
            self.dispositivi[d.mac] = d

    def etichetta(self, mac: str) -> str:
        if mac == self.mio_mac:
            return "questo_computer"
        if mac == self.router_mac:
            return "router"
        if int(mac[:2], 16) & 0x02:
            return "mac_casuale"
        return "nessuna"

    def chi(self, mac: str | None, ip: str | None) -> str:
        """Il modo piu' umano di chiamare un dispositivo: nome, poi "questo computer", poi IP."""
        scheda = self.dispositivi.get(mac) if mac else None
        if scheda and scheda.nome:
            return scheda.nome
        if mac == self.mio_mac:
            return "questo computer"
        if mac == self.router_mac:
            return "router"
        return ip or mac or "?"

    def _segnala_piena(self, ora: float, eventi: list[Evento]) -> None:
        if not self.piena:
            self.piena = True
            eventi.append(Evento(ora, "avviso", "evento.memoria_piena", {"massimo": self.max_dispositivi}))

    def osserva(self, p: Pacchetto, ora: float) -> list[Evento]:
        eventi: list[Evento] = []
        ip_noto = self._arp(p, ora, eventi)
        if ip_noto is None and _ipv4_locale(p.ip_sorgente):
            ip_noto = p.ip_sorgente
        self._dispositivo(p, ora, ip_noto, eventi)
        self._nomi(p)
        self._dns(p, ora, eventi)
        self._connessione(p, ora, eventi)
        self._chiaro(p, ora, eventi)
        eventi += self.rilevatori.osserva(p, ora)
        return eventi

    def _arp(self, p: Pacchetto, ora: float, eventi: list[Evento]) -> str | None:
        if not (p.arp and _ipv4_locale(p.arp.ip) and _unicast(p.arp.mac)):
            return None
        prima = self.arp.get(p.arp.ip)
        if prima is None:
            if len(self.arp) < self.max_dispositivi:
                self.arp[p.arp.ip] = p.arp.mac
            else:
                self._segnala_piena(ora, eventi)
        elif prima != p.arp.mac:
            self.arp[p.arp.ip] = p.arp.mac
            eventi += self.rilevatori.cambio_arp(p.arp.ip, prima, p.arp.mac, ora)
        return p.arp.ip if p.arp.mac == p.mac_sorgente else None

    def _dispositivo(self, p: Pacchetto, ora: float, ip_noto: str | None, eventi: list[Evento]) -> None:
        mac = p.mac_sorgente
        if _unicast(mac):
            scheda = self.dispositivi.get(mac)
            if scheda is not None and ora - scheda.ultimo >= RITORNO:
                eventi.append(Evento(ora, "info", "evento.ritorno", {
                    "mac": mac, "ip": ip_noto or scheda.ip or "-", "chi": self.chi(mac, ip_noto or scheda.ip),
                    "minuti": str(int((ora - scheda.ultimo) // 60))}))
            if scheda is None:
                if len(self.dispositivi) >= self.max_dispositivi:
                    self._segnala_piena(ora, eventi)
                    return
                if ip_noto is None and mac == self.mio_mac:
                    ip_noto = self.mio_ip
                scheda = Dispositivo(mac, ora, ora, 0, ip_noto, self._produttore(mac))
                self.dispositivi[mac] = scheda
                eventi.append(Evento(ora, "info", "evento.nuovo_dispositivo",
                                     {"mac": mac, "ip": ip_noto or "-", "produttore": scheda.produttore or "",
                                      "etichetta": self.etichetta(mac)}))
            scheda.ultimo = ora
            scheda.pacchetti += 1
            scheda.byte_inviati += p.lunghezza
            if ip_noto:
                scheda.ip = ip_noto
            if p.nome_host and p.nome_host != scheda.nome:
                scheda.nome = p.nome_host
                eventi.append(Evento(ora, "info", "evento.nome", {"mac": mac, "nome": p.nome_host}))
        destinatario = self.dispositivi.get(p.mac_destinazione) if p.mac_destinazione else None
        if destinatario is not None:
            destinatario.byte_ricevuti += p.lunghezza

    def _nomi(self, p: Pacchetto) -> None:
        for nome, ip in p.risposte_dns:
            _ricorda(self.nomi_ip, ip, nome, MAX_NOMI_IP)
        if p.sni and p.ip_destinazione:
            _ricorda(self.nomi_ip, p.ip_destinazione, p.sni, MAX_NOMI_IP)

    def _dns(self, p: Pacchetto, ora: float, eventi: list[Evento]) -> None:
        if not (p.dns and p.ip_sorgente):
            return
        chiave = (p.ip_sorgente, p.dns.lower())
        ultima = self.dns.get(chiave)
        if p.mac_sorgente == self.mio_mac:
            self.dns_in_chiaro_mio += 1
        if ultima is None or ora - ultima >= DNS_SILENZIO:
            eventi.append(Evento(ora, "info", "evento.dns",
                                 {"ip": p.ip_sorgente, "chi": self.chi(p.mac_sorgente, p.ip_sorgente), "nome": p.dns}))
        _ricorda(self.dns, chiave, ora, self.max_dns)

    def _dove(self, p: Pacchetto) -> str:
        return p.sni or (p.chiaro.dove if p.chiaro and p.chiaro.dove else None) or self.nomi_ip.get(
            p.ip_destinazione or "", p.ip_destinazione or "?")

    def _connessione(self, p: Pacchetto, ora: float, eventi: list[Evento]) -> None:
        if not (p.nuova_connessione and p.protocollo and p.porta_destinazione is not None):
            return
        if not (_ip_locale(p.ip_sorgente) and _destinazione_valida(p.ip_destinazione)):
            return
        servizio = SERVIZI.get((p.protocollo, p.porta_destinazione))
        if servizio is None:
            if p.protocollo == "udp" and p.porta_destinazione >= 1024:
                return   # troppo rumore: giochi, chiamate, streaming usano porte alte a caso
            servizio = f"{p.protocollo.upper()} {p.porta_destinazione}"
        dove = self._dove(p)
        scheda = self.dispositivi.get(p.mac_sorgente or "")
        if scheda is not None:
            if dove in scheda.destinazioni or len(scheda.destinazioni) < MAX_DESTINAZIONI:
                scheda.destinazioni[dove] = scheda.destinazioni.get(dove, 0) + 1
        in_entrata = self.mio_ip is not None and p.ip_destinazione == self.mio_ip
        # in entrata: una riga per dispositivo ogni 5 minuti, cosi' una scansione non inonda Live
        chiave = (p.ip_sorgente, "in entrata") if in_entrata else (p.ip_sorgente, dove, servizio)
        ultima = self.connessioni.get(chiave)
        if ultima is None or ora - ultima >= CONNESSIONE_SILENZIO:
            eventi.append(Evento(ora, "info", "evento.connessione",
                                 {"chi": self.chi(p.mac_sorgente, p.ip_sorgente), "ip": p.ip_sorgente or "",
                                  "dove": dove, "ip_dove": p.ip_destinazione or "", "servizio": servizio}))
        _ricorda(self.connessioni, chiave, ora, MAX_CONNESSIONI)

    def _chiaro(self, p: Pacchetto, ora: float, eventi: list[Evento]) -> None:
        if p.chiaro is None or not p.ip_sorgente:
            return
        chi = self.chi(p.mac_sorgente, p.ip_sorgente)
        dove = self._dove(p)
        if (p.chiaro.protocollo == "HTTP" and dove.lower() in CONTROLLI_CONNESSIONE
                and not p.chiaro.credenziali):
            chiave_controllo = (p.ip_sorgente, "controllo", dove)
            ultima = self.connessioni.get(chiave_controllo)
            if ultima is None or ora - ultima >= CHIARO_SILENZIO:
                eventi.append(Evento(ora, "info", "evento.controllo_connessione",
                                     {"chi": chi, "ip": p.ip_sorgente, "dove": dove}))
            _ricorda(self.connessioni, chiave_controllo, ora, MAX_CONNESSIONI)
            return
        chiave = (p.ip_sorgente, p.chiaro.protocollo, dove)
        visto = self.chiaro.get(chiave)
        nuova_password = p.chiaro.credenziali and not (visto and visto.credenziali)
        if visto is None:
            visto = InChiaro(ora, ora, chi, p.chiaro.protocollo, dove, p.chiaro.credenziali)
            avvisa = True
        else:
            avvisa = ora - visto.ultimo >= CHIARO_SILENZIO
            visto.ultimo, visto.volte = ora, visto.volte + 1
            visto.credenziali = visto.credenziali or p.chiaro.credenziali
        _ricorda(self.chiaro, chiave, visto, MAX_CHIARO)
        valori = {"chi": chi, "ip": p.ip_sorgente, "protocollo": p.chiaro.protocollo, "dove": dove}
        if nuova_password:
            eventi.append(Evento(ora, "grave", "evento.chiaro_password", valori))
        elif avvisa:
            eventi.append(Evento(ora, "avviso", "evento.chiaro", valori))
