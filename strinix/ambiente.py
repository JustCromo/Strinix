"""`strinix verifica`: controlla che la macchina sia pronta e spiega cosa manca."""

from __future__ import annotations

import os
import platform
import pwd
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from strinix import config as cfg
from strinix import rete, sorgente
from strinix.decodifica import scapy_disponibile


@dataclass(frozen=True)
class Controllo:
    esito: str                      # "ok", "avviso", "errore"
    chiave: str                     # testo in strinix/lingue
    valori: dict = field(default_factory=dict)


def modificabile_da_me(percorso: Path) -> bool:
    """True se il mio utente puo' cambiare il file o una delle cartelle che lo contengono."""
    return any(os.access(p, os.W_OK) for p in (percorso, *percorso.parents))


def controlla(percorso_config: Path | None = None, *, sys_net: Path = rete.SYS_NET,
              route: Path = rete.PROC_ROUTE, euid: int | None = None,
              custode: Path = sorgente.CUSTODE) -> list[Controllo]:
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

    esiti.append(Controllo("ok", "verifica.scapy_ok") if scapy_disponibile()
                 else Controllo("errore", "verifica.scapy_manca"))

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

    if shutil.which("sudo") is None:
        esiti.append(Controllo("errore", "verifica.sudo_manca"))
    else:
        try:
            pwd.getpwnam("nobody")
        except KeyError:
            esiti.append(Controllo("errore", "verifica.nobody_manca"))
        else:
            esiti.append(Controllo("ok", "verifica.cattura_ok"))
    if euid != 0 and modificabile_da_me(custode):
        esiti.append(Controllo("avviso", "verifica.cattura_sviluppo"))
    return esiti
