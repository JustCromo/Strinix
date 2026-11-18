"""I rilevatori: le regole che riconoscono un comportamento sospetto in rete.

Ognuno guarda solo cose che il tuo portatile vede davvero (il tuo traffico e i messaggi
broadcast di tutti) e usa soglie prudenti: un falso allarme insegna a ignorare gli avvisi.

- scansione della rete: tante richieste ARP verso indirizzi diversi in pochi secondi
- scansione delle porte: qualcuno bussa a tante porte di questo computer
- ARP spoofing, a livelli: il router che cambia MAC, un MAC che si prende tanti IP,
  un IP che rimbalza tra due MAC, oppure il caso innocuo di un IP riassegnato
- DHCP falso: un secondo server che distribuisce indirizzi (e magari un router finto)
- DNS sospetti: nomi lunghissimi (tunneling) o generati a caso (tipici dei malware)

Ogni stato ha un limite fisso: chi inonda la rete non puo' riempire la memoria.
"""

from __future__ import annotations

import math
from collections import Counter, OrderedDict, deque
from typing import TYPE_CHECKING

from strinix.decodifica import Pacchetto

if TYPE_CHECKING:
    from strinix.stato import Evento, Memoria

FINESTRA = 10.0              # secondi in cui si contano richieste e porte
SOGLIA_SWEEP = 20            # indirizzi diversi cercati via ARP in 10 s
SOGLIA_SWEEP_GRAVE = 100
SOGLIA_PORTE = 15            # porte diverse di questo computer in 10 s
SOGLIA_PORTE_GRAVE = 100
SOGLIA_MOLTI_IP = 3          # IP diversi rivendicati dallo stesso MAC
ALTALENA = 3                 # cambi dello stesso IP in un minuto
ASSENTE = 600.0              # se il vecchio proprietario non si vede da 10 minuti, l'IP e' stato riassegnato
SILENZIO = 300.0             # lo stesso avviso non si ripete prima di 5 minuti
MAX_CHIAVI = 1024
MAX_PER_CHIAVE = 512
DNS_MAX = 100                # oltre questa lunghezza un nome e' sospetto (tunneling)
DNS_MAX_ETICHETTA = 50
ENTROPIA_CASUALE = 3.5
LUNGHEZZA_CASUALE = 12
VOCALI = set("aeiouy")


class Contatore:
    """Quanti valori diversi ha prodotto ogni chiave negli ultimi FINESTRA secondi (con limiti fissi)."""

    def __init__(self, finestra: float = FINESTRA) -> None:
        self.finestra = finestra
        self._dati: OrderedDict[object, deque] = OrderedDict()

    def aggiungi(self, chiave: object, valore: object, ora: float) -> set:
        coda = self._dati.get(chiave)
        if coda is None:
            coda = self._dati[chiave] = deque(maxlen=MAX_PER_CHIAVE)
            while len(self._dati) > MAX_CHIAVI:
                self._dati.popitem(last=False)
        self._dati.move_to_end(chiave)
        coda.append((ora, valore))
        while coda and ora - coda[0][0] > self.finestra:
            coda.popleft()
        return {v for _, v in coda}


def entropia(testo: str) -> float:
    """Quanto e' "disordinato" un testo (bit per carattere): le parole vere stanno sotto 3.5."""
    if not testo:
        return 0.0
    conti = Counter(testo)
    return -sum(n / len(testo) * math.log2(n / len(testo)) for n in conti.values())


def dominio_casuale(nome: str) -> bool:
    """Il nome principale (es. "xjw9q2kz8fpl" in xjw9q2kz8fpl.com) sembra generato a caso?"""
    etichette = nome.lower().rstrip(".").split(".")
    if len(etichette) < 2 or etichette[-1] in ("local", "arpa", "lan", "home", "internal"):
        return False
    principale = etichette[-2]
    if len(principale) < LUNGHEZZA_CASUALE or principale.startswith("xn--"):   # xn-- = nomi con accenti/alfabeti non latini
        return False
    lettere = [c for c in principale if c.isalpha()]
    vocali = sum(c in VOCALI for c in lettere) / max(1, len(lettere))
    cifre = sum(c.isdigit() for c in principale)
    return entropia(principale) >= ENTROPIA_CASUALE and (vocali < 0.3 or cifre >= 3)


def dominio_lungo(nome: str) -> bool:
    return len(nome) > DNS_MAX or any(len(e) > DNS_MAX_ETICHETTA for e in nome.split("."))


class Rilevatori:
    def __init__(self, memoria: Memoria) -> None:
        self.m = memoria
        self._arp_cercati = Contatore()
        self._porte = Contatore()
        self._cambi: OrderedDict[str, deque] = OrderedDict()
        self._ultimo: OrderedDict[tuple, float] = OrderedDict()   # (tipo, chiave) -> ora dell'ultimo avviso
        self._livello: dict[tuple, str] = {}
        self.server_dhcp: set[str] = set()                      # i server DHCP considerati legittimi

    # ---------------------------------------------------------------- utilita'

    def _evento(self, ora: float, livello: str, tipo: str, chiave: object, valori: dict) -> list[Evento]:
        """Un evento, a meno che lo stesso non sia gia' stato dato da poco (salvo che sia diventato piu' grave)."""
        from strinix.stato import Evento

        k = (tipo, chiave)
        prima = self._ultimo.get(k)
        peggiorato = self._livello.get(k) != "grave" and livello == "grave"
        if prima is not None and ora - prima < SILENZIO and not peggiorato:
            return []
        self._ultimo[k] = ora
        self._ultimo.move_to_end(k)
        self._livello[k] = livello
        while len(self._ultimo) > MAX_CHIAVI:
            vecchio, _ = self._ultimo.popitem(last=False)
            self._livello.pop(vecchio, None)
        return [Evento(ora, livello, "evento." + tipo, valori)]

    # ---------------------------------------------------------------- per ogni pacchetto

    def osserva(self, p: Pacchetto, ora: float) -> list[Evento]:
        eventi: list[Evento] = []
        eventi += self._sweep(p, ora)
        eventi += self._scansione_porte(p, ora)
        eventi += self._dhcp(p, ora)
        eventi += self._dns(p, ora)
        return eventi

    def _sweep(self, p: Pacchetto, ora: float) -> list[Evento]:
        if not (p.arp and p.arp.operazione == 1 and p.arp.ip_cercato != p.arp.ip):
            return []
        cercati = self._arp_cercati.aggiungi(p.arp.mac, p.arp.ip_cercato, ora)
        if len(cercati) < SOGLIA_SWEEP:
            return []
        if p.arp.mac in (self.m.router_mac, self.m.mio_mac):
            livello = "info"          # il router (o tu) che controlla chi c'e': normale
        else:
            livello = "grave" if len(cercati) >= SOGLIA_SWEEP_GRAVE else "avviso"
        return self._evento(ora, livello, "scansione_rete", p.arp.mac, {
            "chi": self.m.chi(p.arp.mac, p.arp.ip), "mac": p.arp.mac, "ip": p.arp.ip,
            "quanti": len(cercati), "secondi": int(FINESTRA)})

    def _scansione_porte(self, p: Pacchetto, ora: float) -> list[Evento]:
        if not (p.nuova_connessione and p.ip_sorgente and p.porta_destinazione is not None):
            return []
        if self.m.mio_ip is None or p.ip_destinazione != self.m.mio_ip or p.ip_sorgente == self.m.mio_ip:
            return []
        if p.protocollo == "udp" and p.porta_destinazione >= 1024:
            return []                 # le risposte UDP arrivano su porte alte a caso: non e' una scansione
        porte = self._porte.aggiungi(p.ip_sorgente, (p.protocollo, p.porta_destinazione), ora)
        if len(porte) < SOGLIA_PORTE:
            return []
        livello = "grave" if len(porte) >= SOGLIA_PORTE_GRAVE else "avviso"
        elenco = ", ".join(str(n) for _, n in sorted(porte, key=lambda x: x[1])[:10])
        return self._evento(ora, livello, "scansione_porte", p.ip_sorgente, {
            "chi": self.m.chi(p.mac_sorgente, p.ip_sorgente), "ip": p.ip_sorgente, "mac": p.mac_sorgente or "",
            "quanti": len(porte), "porte": elenco + ("…" if len(porte) > 10 else ""), "secondi": int(FINESTRA)})

    def _dhcp(self, p: Pacchetto, ora: float) -> list[Evento]:
        if p.dhcp_tipo not in (2, 5) or not p.mac_sorgente:     # solo offerte e conferme dei server
            return []
        server = p.dhcp_server or p.ip_sorgente or p.mac_sorgente
        legittimi = {x for x in (self.m.router_ip, self.m.router_mac) if x}
        if server in legittimi or p.mac_sorgente in legittimi or server in self.server_dhcp:
            self.server_dhcp.add(server)
            return []
        if not legittimi and not self.server_dhcp:
            self.server_dhcp.add(server)    # nessun riferimento: il primo server visto e' quello della rete
            return []
        if self.m.router_ip and p.dhcp_router == self.m.router_ip:
            self.server_dhcp.add(server)    # un server diverso dal router, ma che indica il router vero:
            return []                       # e' normale nelle reti grandi (scuole, uffici)
        livello = "grave" if p.dhcp_router else "avviso"   # il trucco vero e' proporre un router falso
        return self._evento(ora, livello, "dhcp_falso", p.mac_sorgente, {
            "chi": self.m.chi(p.mac_sorgente, server), "mac": p.mac_sorgente, "ip": server,
            "router": p.dhcp_router or "-", "vero_router": self.m.router_ip or "-"})

    def _dns(self, p: Pacchetto, ora: float) -> list[Evento]:
        if not (p.dns and p.ip_sorgente):
            return []
        nome = p.dns.lower()
        principale = ".".join(nome.rstrip(".").split(".")[-2:])
        valori = {"chi": self.m.chi(p.mac_sorgente, p.ip_sorgente), "ip": p.ip_sorgente, "nome": p.dns[:120]}
        if dominio_lungo(nome):
            return self._evento(ora, "avviso", "dns_lungo", (p.ip_sorgente, principale),
                                {**valori, "lunghezza": len(nome), "dominio": principale})
        if dominio_casuale(nome):
            return self._evento(ora, "avviso", "dns_casuale", principale, {**valori, "dominio": principale})
        return []

    # ---------------------------------------------------------------- ARP spoofing a livelli

    def cambio_arp(self, ip: str, prima: str, nuovo: str, ora: float) -> list[Evento]:
        """Lo stesso IP ora risponde da un altro MAC: quanto e' grave?"""
        cambi = self._cambi.get(ip)
        if cambi is None:
            cambi = self._cambi[ip] = deque(maxlen=32)
            while len(self._cambi) > MAX_CHIAVI:
                self._cambi.popitem(last=False)
        cambi.append(ora)
        recenti = sum(1 for x in cambi if ora - x <= 60)
        valori = {"ip": ip, "prima": prima, "ora": nuovo, "chi": self.m.chi(nuovo, None)}
        quanti_ip = sum(1 for m in self.m.arp.values() if m == nuovo)
        vecchio = self.m.dispositivi.get(prima)
        if ip == self.m.router_ip:
            return self._evento(ora, "grave", "arp_router", ip, valori)
        if nuovo != self.m.router_mac and quanti_ip >= SOGLIA_MOLTI_IP:
            return self._evento(ora, "grave", "arp_molti_ip", nuovo, {**valori, "quanti": quanti_ip})
        if recenti >= ALTALENA:
            return self._evento(ora, "grave", "arp_altalena", ip, {**valori, "volte": recenti})
        if vecchio is not None and ora - vecchio.ultimo >= ASSENTE:
            return self._evento(ora, "info", "arp_riassegnato", ip, valori)
        return self._evento(ora, "avviso", "arp_cambio", ip, valori)
