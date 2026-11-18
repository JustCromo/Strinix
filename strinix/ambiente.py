"""`strinix verifica`: controlla che la macchina sia pronta e spiega cosa manca."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

from strinix import config as cfg
from strinix import rete

CAP_NET_RAW = 13  # il permesso del kernel che serve per leggere i pacchetti grezzi


@dataclass(frozen=True)
class Controllo:
    esito: str                      # "ok", "avviso", "errore"
    chiave: str                     # testo in strinix/lingue
    valori: dict = field(default_factory=dict)


def capacita_effettive(status: Path = Path("/proc/self/status")) -> int:
    try:
        for riga in status.read_text(encoding="ascii", errors="replace").splitlines():
            if riga.startswith("CapEff:"):
                return int(riga.split()[1], 16)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def puo_catturare(cap: int | None = None) -> bool:
    cap = capacita_effettive() if cap is None else cap
    return bool(cap >> CAP_NET_RAW & 1)


def controlla(percorso_config: Path | None = None, *, sys_net: Path = rete.SYS_NET,
              route: Path = rete.PROC_ROUTE, cap: int | None = None, euid: int | None = None) -> list[Controllo]:
    euid = os.geteuid() if euid is None else euid
    esiti: list[Controllo] = []

    v = sys.version_info
    versione = f"{v.major}.{v.minor}.{v.micro}"
    esiti.append(Controllo("ok" if v >= (3, 11) else "errore",
                           "verifica.python_ok" if v >= (3, 11) else "verifica.python_vecchio", {"versione": versione}))

    sistema = platform.system()
    esiti.append(Controllo("ok" if sistema == "Linux" else "errore",
                           "verifica.sistema_ok" if sistema == "Linux" else "verifica.sistema_no", {"sistema": sistema}))

    if euid == 0:
        esiti.append(Controllo("avviso", "verifica.root"))

    esiti.append(Controllo("ok", "verifica.scapy_ok") if importlib.util.find_spec("scapy")
                 else Controllo("avviso", "verifica.scapy_manca"))

    richiesta = "auto"
    try:
        conf = cfg.carica(percorso_config)
        richiesta = conf.interfaccia
        if conf.percorso is None:
            esiti.append(Controllo("ok", "verifica.config_predefinita",
                                   {"percorso": percorso_config or cfg.percorso_predefinito()}))
        else:
            esiti.append(Controllo("ok", "verifica.config_letta", {"percorso": conf.percorso}))
    except cfg.ConfigError as exc:
        esiti.append(Controllo("errore", exc.chiave, exc.valori))

    interfacce = rete.elenca(sys_net)
    strada = rete.strada_predefinita(route)
    scelta = rete.scegli(richiesta, interfacce, strada)
    if scelta is None:
        esiti.append(Controllo("errore", "verifica.interfaccia_manca" if richiesta == "auto"
                               else "verifica.interfaccia_inesistente", {"nome": richiesta}))
    else:
        esiti.append(Controllo("ok" if scelta.stato == "attiva" else "avviso",
                               "verifica.interfaccia_ok" if scelta.stato == "attiva" else "verifica.interfaccia_spenta",
                               {"nome": scelta.nome, "tipo": scelta.tipo, "ip": scelta.ipv4 or "-"}))

    if strada:
        esiti.append(Controllo("ok", "verifica.router_ok", {"ip": strada.gateway, "nome": strada.interfaccia}))
    else:
        esiti.append(Controllo("avviso", "verifica.router_manca"))

    esiti.append(Controllo("ok", "verifica.cattura_ok") if puo_catturare(cap)
                 else Controllo("avviso", "verifica.cattura_manca"))
    return esiti
