"""Una sessione di ascolto: custode + decodifica + memoria, in un thread separato.

Il cockpit non tocca mai i pacchetti: chiede ogni tanto una `Istantanea`, cioe'
una copia immutabile di quello che la sessione sa in quel momento. Quando
arrivera' l'agente in background, l'istantanea verra' da li' invece che da qui,
e il cockpit restera' uguale.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from strinix.decodifica import decodifica as _decodifica
from strinix.rete import Interfaccia, Strada
from strinix.sorgente import Cattura, CatturaError
from strinix.stato import Dispositivo, Evento, Memoria

MAX_EVENTI = 2000
FINESTRA_PPS = 5  # secondi su cui si calcolano i pacchetti al secondo


@dataclass(frozen=True)
class Istantanea:
    interfaccia: Interfaccia | None
    strada: Strada | None
    router_mac: str | None
    attiva: bool
    errore: tuple[str, dict] | None
    inizio: float
    pacchetti: int
    illeggibili: int
    pps: float
    eventi: tuple[Evento, ...]
    dispositivi: tuple[Dispositivo, ...]
    etichette: dict = field(default_factory=dict)   # MAC -> etichetta (router, questo_computer...)
    dns_in_chiaro_mio: int = 0

    @property
    def avvisi(self) -> tuple[Evento, ...]:
        return tuple(e for e in self.eventi if e.livello != "info")

    @property
    def dns_cifrato(self) -> str:
        """"si'", "no" oppure "presto": il browser usa DNS cifrato? (stima, non certezza)"""
        if self.dns_in_chiaro_mio:
            return "no"
        if time.time() - self.inizio >= 30 and self.pacchetti >= 200:
            return "probabile"
        return "presto"


def istantanea_vuota(interfaccia: Interfaccia | None = None, strada: Strada | None = None) -> Istantanea:
    return Istantanea(interfaccia, strada, None, False, None, time.time(), 0, 0, 0.0, (), ())


class Sessione:
    def __init__(self, interfaccia: Interfaccia, strada: Strada | None, router_mac: str | None = None, *,
                 cattura: Callable[[str], Cattura] = Cattura,
                 decodifica: Callable = _decodifica, max_eventi: int = MAX_EVENTI) -> None:
        self.interfaccia, self.strada, self.router_mac = interfaccia, strada, router_mac
        self._crea_cattura, self._decodifica = cattura, decodifica
        self.memoria = Memoria(mio_mac=interfaccia.mac, router_mac=router_mac, mio_ip=interfaccia.ipv4)
        self._eventi: deque[Evento] = deque(maxlen=max_eventi)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cattura: Cattura | None = None
        self.pacchetti = self.illeggibili = 0
        self._secondi: deque[list[int]] = deque(maxlen=FINESTRA_PPS + 1)   # [secondo, pacchetti]
        self.attiva = False
        self.errore: tuple[str, dict] | None = None
        self.inizio = time.time()

    def avvia(self) -> None:
        """Avvia il custode e aspetta che sia pronto (sudo chiede qui la password). Lancia CatturaError."""
        self._cattura = self._crea_cattura(self.interfaccia.nome)
        self._cattura.__enter__()
        try:
            self._cattura.attendi()
        except BaseException:
            self._cattura.__exit__(None, None, None)
            raise
        self.attiva = True
        self.inizio = time.time()
        self._thread = threading.Thread(target=self._ciclo, name="strinix-ascolto", daemon=True)
        self._thread.start()

    def _conta(self, ora: float) -> None:
        secondo = int(ora)
        if self._secondi and self._secondi[-1][0] == secondo:
            self._secondi[-1][1] += 1
        else:
            self._secondi.append([secondo, 1])

    def _ciclo(self) -> None:
        assert self._cattura is not None
        try:
            for frame in self._cattura.frame():
                try:
                    p = self._decodifica(frame.dati)
                except ValueError:
                    with self._lock:
                        self.pacchetti += 1
                        self.illeggibili += 1
                    continue
                with self._lock:
                    self.pacchetti += 1
                    self._conta(time.time())
                    self._eventi.extend(self.memoria.osserva(p, frame.ora))
        except CatturaError as exc:
            with self._lock:
                self.errore = (exc.chiave, dict(exc.valori))
        except (OSError, ValueError):
            pass  # flusso chiuso mentre ci fermiamo
        finally:
            self.attiva = False

    def _pps(self, adesso: float) -> float:
        corrente = int(adesso)
        totale = sum(n for s, n in self._secondi if corrente - FINESTRA_PPS <= s < corrente)
        return totale / FINESTRA_PPS

    def istantanea(self) -> Istantanea:
        with self._lock:
            schede = tuple(replace(d) for d in self.memoria.dispositivi.values())
            return Istantanea(
                interfaccia=self.interfaccia, strada=self.strada, router_mac=self.router_mac,
                attiva=self.attiva, errore=self.errore, inizio=self.inizio,
                pacchetti=self.pacchetti, illeggibili=self.illeggibili, pps=self._pps(time.time()),
                eventi=tuple(self._eventi), dispositivi=schede,
                etichette={d.mac: self.memoria.etichetta(d.mac) for d in schede},
                dns_in_chiaro_mio=self.memoria.dns_in_chiaro_mio,
            )

    def ferma(self) -> None:
        if self._cattura is None:
            return
        self._cattura.chiudi_ingresso()       # il custode si ferma da solo
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._cattura.__exit__(None, None, None)
        self.attiva = False
