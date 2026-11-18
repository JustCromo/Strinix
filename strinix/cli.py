"""Punto d'ingresso: `strinix <comando>`.

Come in Erinix c'e' un registro dei comandi: ogni comando ha un nome, una riga
d'aiuto e una funzione. Per aggiungerne uno basta una voce in COMANDI.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from strinix import __version__, ambiente, rete
from strinix import config as cfg
from strinix.decodifica import decodifica, scapy_disponibile
from strinix.messaggi import imposta_lingua, t
from strinix.sorgente import Cattura, CatturaError
from strinix.stato import Evento, Memoria
from strinix.terminale import Stile, colori_attivi

SIMBOLI = {"ok": "✔", "avviso": "!", "errore": "✘"}


@dataclass(frozen=True)
class Comando:
    nome: str
    aiuto: str
    esegui: Callable[[list[str], Stile, TextIO], int]


def _aiuto(args: list[str], stile: Stile, out: TextIO) -> int:
    print(t("cli.descrizione"), file=out)
    print(file=out)
    print(t("cli.uso"), file=out)
    print(t("cli.comandi"), file=out)
    for c in COMANDI.values():
        print(f"  {stile('titolo', c.nome.ljust(11))} {t(c.aiuto)}", file=out)
    return 0


def _versione(args: list[str], stile: Stile, out: TextIO) -> int:
    print(t("versione", versione=__version__), file=out)
    return 0


def _verifica(args: list[str], stile: Stile, out: TextIO) -> int:
    print(stile("titolo", t("verifica.titolo")), file=out)
    esiti = ambiente.controlla()
    for e in esiti:
        print(f"  {stile(e.esito, SIMBOLI[e.esito])} {t(e.chiave, **e.valori)}", file=out)
    errori = sum(e.esito == "errore" for e in esiti)
    avvisi = sum(e.esito == "avviso" for e in esiti)
    if errori:
        print(stile("errore", t("verifica.esito_errori", numero=errori)), file=out)
        return 1
    if avvisi:
        print(stile("avviso", t("verifica.esito_avvisi", numero=avvisi)), file=out)
    else:
        print(stile("ok", t("verifica.esito_pronto")), file=out)
    return 0


def _interfacce(args: list[str], stile: Stile, out: TextIO) -> int:
    print(stile("titolo", t("interfacce.titolo")), file=out)
    interfacce = rete.elenca()
    if not interfacce:
        print("  " + t("interfacce.nessuna"), file=out)
        return 1
    try:
        richiesta = cfg.carica().interfaccia
    except cfg.ConfigError:
        richiesta = "auto"
    scelta = rete.scegli(richiesta, interfacce, rete.strada_predefinita())
    for i in interfacce:
        usata = scelta is not None and i.nome == scelta.nome
        punto = stile("ok", "●") if i.stato == "attiva" else stile("tenue", "○")
        riga = (f"  {punto} {i.nome:<15} {t('interfacce.tipo.' + i.tipo):<9} "
                f"{t('interfacce.stato.' + i.stato):<7} {i.ipv4 or '-':<15} {i.mac or '-'}")
        if usata:
            riga += "  " + stile("titolo", t("interfacce.usata"))
        print(riga, file=out)
    return 0


def _config(args: list[str], stile: Stile, out: TextIO) -> int:
    if args == ["crea"]:
        try:
            percorso = cfg.crea()
        except cfg.ConfigError as exc:
            print(stile("errore", t(exc.chiave, **exc.valori)), file=sys.stderr)
            return 1
        print(stile("ok", t("config.creato", percorso=percorso)), file=out)
        return 0
    if args:
        print(t("config.uso"), file=sys.stderr)
        return 2
    print(stile("titolo", t("config.titolo")), file=out)
    try:
        conf = cfg.carica()
    except cfg.ConfigError as exc:
        print(stile("errore", t(exc.chiave, **exc.valori)), file=sys.stderr)
        return 1
    if conf.percorso is None:
        print("  " + t("config.nessun_file", percorso=cfg.percorso_predefinito()), file=out)
    else:
        print("  " + t("config.file", percorso=conf.percorso), file=out)
    for chiave in cfg.CHIAVI:
        print("  " + t("config.valore", chiave=chiave, valore=getattr(conf, chiave)), file=out)
    return 0


def _riga_evento(e: Evento, stile: Stile) -> str:
    ora = time.strftime("%H:%M:%S", time.localtime(e.ora))
    valori = dict(e.valori)
    if "etichetta" in valori:
        valori["etichetta"] = t("etichetta." + valori["etichetta"])
    simbolo = stile("avviso", "!") if e.livello == "avviso" else stile("ok", "●")
    testo = t(e.chiave, **valori)
    return f"  {stile('tenue', ora)} {simbolo} {stile('avviso', testo) if e.livello == 'avviso' else testo}"


def _ascolta(args: list[str], stile: Stile, out: TextIO) -> int:
    if not scapy_disponibile():
        print(stile("errore", t("verifica.scapy_manca")), file=sys.stderr)
        return 1
    try:
        richiesta = cfg.carica().interfaccia
    except cfg.ConfigError as exc:
        print(stile("errore", t(exc.chiave, **exc.valori)), file=sys.stderr)
        return 1
    interfacce = rete.elenca()
    strada = rete.strada_predefinita()
    scelta = rete.scegli(richiesta, interfacce, strada)
    if scelta is None:
        print(stile("errore", t("verifica.interfaccia_manca")), file=sys.stderr)
        return 1
    router_mac = rete.mac_di(strada.gateway) if strada else None
    memoria = Memoria(mio_mac=scelta.mac, router_mac=router_mac)
    print(stile("titolo", t("ascolta.titolo", nome=scelta.nome)), file=out)
    print(stile("tenue", t("ascolta.sudo")), file=out, flush=True)
    pacchetti = illeggibili = 0
    inizio = time.monotonic()
    codice = 0
    try:
        with Cattura(scelta.nome) as cattura:
            for frame in cattura.frame():
                if pacchetti == 0:
                    print(stile("ok", t("ascolta.avviata")), file=out, flush=True)
                pacchetti += 1
                try:
                    p = decodifica(frame.dati)
                except ValueError:
                    illeggibili += 1
                    continue
                for e in memoria.osserva(p, frame.ora):
                    print(_riga_evento(e, stile), file=out, flush=True)
    except CatturaError as exc:
        print(stile("errore", t(exc.chiave, **exc.valori)), file=sys.stderr)
        codice = 1
    except KeyboardInterrupt:
        pass
    print(file=out)
    print(t("ascolta.riepilogo", pacchetti=pacchetti, secondi=round(time.monotonic() - inizio),
            dispositivi=len(memoria.dispositivi), illeggibili=illeggibili), file=out)
    return codice


COMANDI: dict[str, Comando] = {c.nome: c for c in (
    Comando("ascolta", "aiuto.ascolta", _ascolta),
    Comando("verifica", "aiuto.verifica", _verifica),
    Comando("interfacce", "aiuto.interfacce", _interfacce),
    Comando("config", "aiuto.config", _config),
    Comando("versione", "aiuto.versione", _versione),
    Comando("aiuto", "aiuto.aiuto", _aiuto),
)}
SENZA_ARGOMENTI = {"ascolta", "verifica", "interfacce", "versione", "aiuto"}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    args = list(sys.argv[1:] if argv is None else argv)
    colore = "--no-color" not in args
    args = [a for a in args if a != "--no-color"]
    stile = Stile(colori_attivi(out, colore))
    try:  # la lingua viene dalla configurazione; se e' rotta lo dira' `strinix verifica`
        imposta_lingua(cfg.carica().lingua)
    except (cfg.ConfigError, ValueError):
        pass
    if not args or args[0] in ("-h", "--help"):
        args = ["aiuto"]
    elif args[0] in ("-V", "--version"):
        args = ["versione"]
    comando = COMANDI.get(args[0])
    if comando is None:
        print(t("cli.sconosciuto", comando=args[0]), file=sys.stderr)
        return 2
    if comando.nome in SENZA_ARGOMENTI and len(args) > 1:
        print(t("cli.troppi_argomenti", comando=comando.nome), file=sys.stderr)
        return 2
    return comando.esegui(args[1:], stile, out)


if __name__ == "__main__":
    raise SystemExit(main())
