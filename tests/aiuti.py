"""Una fonte finta per provare il cockpit senza rete e senza sudo."""

import time

from strinix.rete import Interfaccia, Strada
from strinix.sessione import Istantanea
from strinix.stato import Dispositivo, Evento

MIO, ROUTER, TELEFONO = "2c:8d:b1:71:af:ed", "60:d8:a4:25:bb:5b", "16:37:f5:e1:63:98"


class Finta:
    def istantanea(self):
        ora = time.time()
        dispositivi = (Dispositivo(MIO, ora - 60, ora, 900, "192.168.1.187"),
                       Dispositivo(ROUTER, ora - 59, ora, 400, "192.168.1.254"),
                       Dispositivo(TELEFONO, ora - 50, ora - 5, 30, "192.168.1.201"))
        eventi = (
            Evento(ora - 60, "info", "evento.nuovo_dispositivo",
                   {"mac": MIO, "ip": "192.168.1.187", "etichetta": "questo_computer"}),
            Evento(ora - 59, "info", "evento.nuovo_dispositivo",
                   {"mac": ROUTER, "ip": "192.168.1.254", "etichetta": "router"}),
            Evento(ora - 50, "info", "evento.nuovo_dispositivo",
                   {"mac": TELEFONO, "ip": "192.168.1.201", "etichetta": "mac_casuale"}),
            Evento(ora - 30, "info", "evento.dns", {"ip": "192.168.1.187", "nome": "github.com"}),
            Evento(ora - 10, "avviso", "evento.arp_cambio",
                   {"ip": "192.168.1.254", "prima": ROUTER, "ora": "de:ad:be:ef:00:01"}),
        )
        return Istantanea(Interfaccia("wlp0s20f3", "wifi", "attiva", MIO, "192.168.1.187"),
                          Strada("wlp0s20f3", "192.168.1.254"), ROUTER, True, None, ora - 60, 1234, 0, 42.0,
                          eventi, dispositivi, {MIO: "questo_computer", ROUTER: "router", TELEFONO: "mac_casuale"}, 0)
