"""Punto d'ingresso: `strinix` apre il cockpit, `strinix <comando>` esegue ed esce.

Cockpit, console e riga di comando usano la stessa tabella dei comandi
(strinix/comandi/integrati.py).
"""

from __future__ import annotations

import os
import signal
import sys

from strinix import archivio, cronologia, file_sicuri, rete
from strinix import config as cfg
from strinix.comandi.integrati import crea_registro
from strinix.comandi.registro import FALLITO, Contesto
from strinix.decodifica import scapy_disponibile
from strinix.messaggi import imposta_lingua, t
from strinix.sessione import Sessione
from strinix.sorgente import CatturaError
from strinix.tui import schermo
from strinix.ui.tema import Tema

OPZIONI_GLOBALI = ("--no-color", "--plain", "--console")


def main(argv: list[str] | None = None, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    colore = "--no-color" not in args
    semplice = "--plain" in args
    classica = "--console" in args
    args = [a for a in args if a not in OPZIONI_GLOBALI]
    try:  # la lingua viene dalla configurazione; se e' rotta lo dira' `strinix verifica`
        imposta_lingua(cfg.carica().lingua)
    except (cfg.ConfigError, ValueError):
        pass
    tema = Tema.per(out, colore)
    registro = crea_registro()
    if not args:
        if _terminale_vero(out):
            if semplice or classica or os.environ.get("TERM", "dumb") in ("", "dumb"):
                from strinix.tui import console
                return console.esegui(registro, tema, out=out, err=err)
            return _cockpit(registro, Tema.per(out, colore), out, err)
        args = ["aiuto"]
    elif args[0] in ("-h", "--help"):
        args = ["aiuto", *args[1:]]
    elif args[0] in ("-V", "--version"):
        args = ["versione", *args[1:]]
    return registro.esegui(Contesto(tema=tema, out=out, err=err), args)


def _terminale_vero(out) -> bool:
    try:
        return sys.stdin.isatty() and out.isatty()
    except (AttributeError, ValueError):
        return False


def _cockpit(registro, tema: Tema, out, err) -> int:
    """Se l'agente sta ascoltando, il cockpit legge il suo archivio (niente sudo).

    Altrimenti avvia una sessione sua: sudo chiede la password qui, prima di cambiare schermo.
    """
    from strinix.tui import cockpit as ck

    ctx = Contesto(tema=tema, out=out, err=err)
    fonte = archivio.apri_fonte()
    sessione = None
    if fonte is None or not fonte.archivio.agente_vivo():
        if not scapy_disponibile():
            ctx.errore("verifica.scapy_manca")
            return FALLITO
        try:
            richiesta = cfg.carica().interfaccia
        except cfg.ConfigError as exc:
            ctx.errore(exc.chiave, **exc.valori)
            return FALLITO
        strada = rete.strada_predefinita()
        scelta = rete.scegli(richiesta, rete.elenca(), strada)
        if scelta is None:
            ctx.errore("verifica.interfaccia_manca")
            return FALLITO
        sessione = Sessione(scelta, strada, rete.mac_di(strada.gateway) if strada else None)
        if fonte is not None:
            sessione.ricorda(fonte.archivio.dispositivi_noti())   # i dispositivi gia' noti non sono "nuovi"
            fonte.archivio.chiudi()
        print(tema("accento", t("ascolta.titolo", nome=scelta.nome)), file=out)
        print(tema("tenue", t("ascolta.sudo")), file=out, flush=True)
        try:
            sessione.avvia()
        except CatturaError as exc:
            ctx.errore(exc.chiave, **exc.valori)
            return FALLITO
        except KeyboardInterrupt:
            print(file=out)
            return FALLITO
    sorgente = sessione if sessione is not None else fonte
    segno_guida = file_sicuri.cartella_stato() / "guida-vista"
    prima_volta = not segno_guida.exists()
    cockpit = ck.Cockpit(registro, tema, sorgente, storia=cronologia.carica(), prima_volta=prima_volta)
    try:
        ck.esegui(cockpit, sys.stdin.fileno(), out)
    except SystemExit:
        segnale = schermo.ultimo_segnale()
        if segnale is not None:
            print(t("cockpit.segnale", segnale=signal.Signals(segnale).name), file=err)
        raise
    finally:
        if sessione is not None:
            sessione.ferma()
        else:
            fonte.archivio.chiudi()
        cronologia.salva(cockpit.storia)
        if prima_volta:
            try:
                file_sicuri.scrivi(segno_guida, "", sovrascrivi=False)
            except (file_sicuri.FileNonSicuro, OSError):
                pass
    print(t("cockpit.chiuso" if sessione is not None else "cockpit.chiuso_agente"), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
