"""I comandi di Strinix. Per aggiungerne uno: una funzione e una voce in `crea_registro()`."""

from __future__ import annotations

import time

from strinix import __version__, ambiente, rete
from strinix import config as cfg
from strinix.comandi.registro import FALLITO, GRUPPI, OK, USO, Argomenti, Comando, Contesto, Registro
from strinix.decodifica import decodifica, scapy_disponibile
from strinix.messaggi import t
from strinix.sessione import Istantanea
from strinix.sorgente import Cattura, CatturaError
from strinix.stato import Evento, Memoria
from strinix.ui import tema as tm

JSON = frozenset({"--json"})


def _ora(secondi: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(secondi))


def testo_evento(e: Evento) -> str:
    valori = dict(e.valori)
    if "etichetta" in valori:
        valori["etichetta"] = t("etichetta." + valori["etichetta"])
    if e.chiave == "evento.nuovo_dispositivo":
        valori.setdefault("produttore", "")
    if "produttore" in valori:
        valori["produttore"] = f" {valori['produttore']}" if valori["produttore"] else ""
    return t(e.chiave, **valori)


def evento_json(e: Evento) -> dict:
    return {"ora": round(e.ora, 3), "livello": e.livello, "tipo": e.chiave.removeprefix("evento."),
            "testo": testo_evento(e), "dati": {k: str(v) for k, v in e.valori.items()}}


# ---------------------------------------------------------------- generale

def _aiuto(ctx: Contesto, args: Argomenti) -> int:
    registro = crea_registro()
    tema = ctx.tema
    if args.liberi:
        comando, resto = registro.trova(list(args.liberi))
        if comando is None or resto or comando.nascosto:
            sotto = registro.sottocomandi(args.liberi[0])
            if sotto and not resto:
                for c in sotto:
                    ctx.scrivi(f"  {tema('accento', c.nome.ljust(16))} {c.descrizione}")
                return OK
            vicino = registro.suggerisci(args.liberi[0])
            if vicino:
                ctx.errore("cli.sconosciuto_suggerimento", comando=args.liberi[0], suggerimento=vicino)
            else:
                ctx.errore("cli.sconosciuto", comando=args.liberi[0])
            return USO
        ctx.scrivi(comando.descrizione)
        ctx.scrivi(t("cli.uso", uso=comando.uso))
        sotto = registro.sottocomandi(comando.parole[0]) if len(comando.parole) == 1 else []
        for c in sotto:
            ctx.scrivi(f"  {tema('accento', c.nome.ljust(16))} {c.descrizione}")
        return OK
    ctx.scrivi(tema("accento", f"{tema.simbolo('gufo')} strinix") + " " + tema("tenue", "· " + t("cli.descrizione")))
    ctx.scrivi()
    if not ctx.interattivo:
        ctx.scrivi(t("cli.modi"))
        ctx.scrivi()
    for gruppo in GRUPPI:
        comandi = [c for c in registro.visibili(ctx.interattivo, ctx.nel_cockpit) if c.gruppo == gruppo]
        if not comandi:
            continue
        ctx.scrivi(tema("dati", t("gruppo." + gruppo)))
        for c in comandi:
            ctx.scrivi(f"  {tema('accento', c.nome.ljust(16))} {c.descrizione}")
    ctx.scrivi()
    ctx.scrivi(tema("tenue", t("cli.aiuto_comando")))
    return OK


def _versione(ctx: Contesto, args: Argomenti) -> int:
    if args.json:
        ctx.stampa_json({"versione": __version__})
    else:
        ctx.scrivi(t("versione", versione=__version__))
    return OK


def _esci(ctx: Contesto, args: Argomenti) -> int:
    ctx.uscita = True
    return OK


# ---------------------------------------------------------------- stato

def _scelta_corrente() -> tuple[rete.Interfaccia | None, rete.Strada | None]:
    try:
        richiesta = cfg.carica().interfaccia
    except cfg.ConfigError:
        richiesta = "auto"
    interfacce = rete.elenca()
    strada = rete.strada_predefinita()
    return rete.scegli(richiesta, interfacce, strada), strada


def _stato(ctx: Contesto, args: Argomenti) -> int:
    ist: Istantanea | None = ctx.sessione.istantanea() if ctx.sessione is not None else None
    interfaccia, strada = (ist.interfaccia, ist.strada) if ist else _scelta_corrente()
    dati = {
        "interfaccia": interfaccia.nome if interfaccia else None,
        "ip": interfaccia.ipv4 if interfaccia else None,
        "router": strada.gateway if strada else None,
        "ascolto": {"attivo": bool(ist and ist.attiva)},
    }
    if ist:
        dati["ascolto"].update({"pacchetti": ist.pacchetti, "pacchetti_al_secondo": round(ist.pps, 1),
                                "dispositivi": len(ist.dispositivi), "avvisi": len(ist.avvisi),
                                "illeggibili": ist.illeggibili, "dns_cifrato": ist.dns_cifrato})
    if args.json:
        ctx.stampa_json(dati)
        return OK
    tema = ctx.tema
    ctx.scrivi(tema("accento", t("stato.titolo")))
    ctx.scrivi("  " + t("stato.interfaccia", nome=dati["interfaccia"] or "-", ip=dati["ip"] or "-"))
    ctx.scrivi("  " + t("stato.router", ip=dati["router"] or "-"))
    if ist and ist.attiva:
        a = dati["ascolto"]
        ctx.scrivi("  " + tema("ok", t("stato.ascolto_attivo", pacchetti=a["pacchetti"], pps=a["pacchetti_al_secondo"])))
        ctx.scrivi("  " + t("stato.numeri", dispositivi=a["dispositivi"], avvisi=a["avvisi"]))
    else:
        ctx.scrivi("  " + tema("tenue", t("stato.ascolto_spento")))
    return OK


def _serve_sessione(ctx: Contesto) -> Istantanea | None:
    if ctx.sessione is None:
        ctx.errore("cli.serve_cockpit")
        return None
    return ctx.sessione.istantanea()


def _dispositivi(ctx: Contesto, args: Argomenti) -> int:
    ist = _serve_sessione(ctx)
    if ist is None:
        return FALLITO
    schede = sorted(ist.dispositivi, key=lambda d: d.primo)
    if args.json:
        adesso = time.time()
        ctx.stampa_json([{"mac": d.mac, "ip": d.ip, "tipo": ist.etichette.get(d.mac, "nessuna"), "nome": d.nome,
                          "produttore": d.produttore, "online": d.online(adesso),
                          "byte_inviati": d.byte_inviati, "byte_ricevuti": d.byte_ricevuti,
                          "primo": round(d.primo, 3), "ultimo": round(d.ultimo, 3), "pacchetti": d.pacchetti}
                         for d in schede])
        return OK
    if not schede:
        ctx.scrivi(t("dispositivi.nessuno"))
    for d in schede:
        tipo = t("tipo." + ist.etichette.get(d.mac, "nessuna"))
        nome = d.nome or d.produttore or tipo or "-"
        ctx.scrivi(f"  {ctx.tema('dati', d.mac)}  {(d.ip or '-'):<15} {nome[:28]:<28} {_ora(d.ultimo)}")
    return OK


def _avvisi(ctx: Contesto, args: Argomenti) -> int:
    ist = _serve_sessione(ctx)
    if ist is None:
        return FALLITO
    if args.json:
        ctx.stampa_json([evento_json(e) for e in ist.avvisi])
        return OK
    if not ist.avvisi:
        ctx.scrivi(t("avvisi.nessuno"))
    for e in ist.avvisi:
        ruolo = "errore" if e.livello == "grave" else "avviso"
        ctx.scrivi(f"  {ctx.tema('tenue', _ora(e.ora))} {ctx.tema(ruolo, testo_evento(e))}")
    return OK


# ---------------------------------------------------------------- ascolto

def _ascolta(ctx: Contesto, args: Argomenti) -> int:
    tema = ctx.tema
    if not scapy_disponibile():
        ctx.errore("verifica.scapy_manca")
        return FALLITO
    try:
        cfg.carica()
    except cfg.ConfigError as exc:
        ctx.errore(exc.chiave, **exc.valori)
        return FALLITO
    scelta, strada = _scelta_corrente()
    if scelta is None:
        ctx.errore("verifica.interfaccia_manca")
        return FALLITO
    router_mac = rete.mac_di(strada.gateway) if strada else None
    memoria = Memoria(mio_mac=scelta.mac, router_mac=router_mac, mio_ip=scelta.ipv4)
    ctx.scrivi(tema("accento", t("ascolta.titolo", nome=scelta.nome)))
    print(tema("tenue", t("ascolta.sudo")), file=ctx.out, flush=True)
    pacchetti = illeggibili = 0
    inizio = time.monotonic()
    codice = OK
    try:
        with Cattura(scelta.nome) as cattura:
            cattura.attendi()
            print(tema("ok", t("ascolta.avviata")), file=ctx.out, flush=True)
            for frame in cattura.frame():
                pacchetti += 1
                try:
                    p = decodifica(frame.dati)
                except ValueError:
                    illeggibili += 1
                    continue
                for e in memoria.osserva(p, frame.ora):
                    ruolo = {"avviso": "avviso", "grave": "errore"}.get(e.livello)
                    simbolo = tema(ruolo, tema.simbolo(ruolo)) if ruolo else tema("ok", tema.simbolo("punto"))
                    testo = tema(ruolo, testo_evento(e)) if ruolo else testo_evento(e)
                    print(f"  {tema('tenue', _ora(e.ora))} {simbolo} {testo}", file=ctx.out, flush=True)
    except CatturaError as exc:
        ctx.errore(exc.chiave, **exc.valori)
        codice = FALLITO
    except KeyboardInterrupt:
        pass
    ctx.scrivi()
    ctx.scrivi(t("ascolta.riepilogo", pacchetti=pacchetti, secondi=round(time.monotonic() - inizio),
                 dispositivi=len(memoria.dispositivi), illeggibili=illeggibili))
    return codice


# ---------------------------------------------------------------- diagnostica

def _verifica(ctx: Contesto, args: Argomenti) -> int:
    esiti = ambiente.controlla()
    errori = sum(e.esito == "errore" for e in esiti)
    avvisi = sum(e.esito == "avviso" for e in esiti)
    if args.json:
        ctx.stampa_json({"pronto": errori == 0, "errori": errori, "avvisi": avvisi,
                         "controlli": [{"esito": e.esito, "codice": e.chiave, "testo": t(e.chiave, **e.valori)}
                                       for e in esiti]})
        return FALLITO if errori else OK
    tema = ctx.tema
    ctx.scrivi(tema("accento", t("verifica.titolo")))
    for e in esiti:
        ctx.scrivi(f"  {tema(e.esito, tema.simbolo(e.esito))} {t(e.chiave, **e.valori)}")
    if errori:
        ctx.scrivi(tema("errore", t("verifica.esito_errori", numero=errori)))
        return FALLITO
    if avvisi:
        ctx.scrivi(tema("avviso", t("verifica.esito_avvisi", numero=avvisi)))
    else:
        ctx.scrivi(tema("ok", t("verifica.esito_pronto")))
    return OK


def _interfacce(ctx: Contesto, args: Argomenti) -> int:
    interfacce = rete.elenca()
    scelta, _ = _scelta_corrente()
    if args.json:
        ctx.stampa_json([{"nome": i.nome, "tipo": i.tipo, "stato": i.stato, "ipv4": i.ipv4, "mac": i.mac,
                          "usata": scelta is not None and i.nome == scelta.nome} for i in interfacce])
        return OK if interfacce else FALLITO
    tema = ctx.tema
    ctx.scrivi(tema("accento", t("interfacce.titolo")))
    if not interfacce:
        ctx.scrivi("  " + t("interfacce.nessuna"))
        return FALLITO
    for i in interfacce:
        punto = tema("ok", tema.simbolo("punto")) if i.stato == "attiva" else tema("tenue", tema.simbolo("vuoto"))
        riga = (f"  {punto} {i.nome:<15} {t('interfacce.tipo.' + i.tipo):<9} "
                f"{t('interfacce.stato.' + i.stato):<7} {i.ipv4 or '-':<15} {tema('dati', i.mac or '-')}")
        if scelta is not None and i.nome == scelta.nome:
            riga += "  " + tema("accento", t("interfacce.usata"))
        ctx.scrivi(riga)
    return OK


# ---------------------------------------------------------------- impostazioni

def _config(ctx: Contesto, args: Argomenti) -> int:
    try:
        conf = cfg.carica()
    except cfg.ConfigError as exc:
        ctx.errore(exc.chiave, **exc.valori)
        return FALLITO
    percorso = conf.percorso or cfg.percorso_predefinito()
    if args.json:
        ctx.stampa_json({"file": str(percorso), "esiste": conf.percorso is not None,
                         **{k: getattr(conf, k) for k in cfg.CHIAVI}})
        return OK
    ctx.scrivi(ctx.tema("accento", t("config.titolo")))
    if conf.percorso is None:
        ctx.scrivi("  " + t("config.nessun_file", percorso=percorso))
    else:
        ctx.scrivi("  " + t("config.file", percorso=percorso))
    for chiave in cfg.CHIAVI:
        ctx.scrivi("  " + t("config.valore", chiave=chiave, valore=getattr(conf, chiave)))
    return OK


def _config_crea(ctx: Contesto, args: Argomenti) -> int:
    try:
        percorso = cfg.crea()
    except cfg.ConfigError as exc:
        ctx.errore(exc.chiave, **exc.valori)
        return FALLITO
    ctx.scrivi(ctx.tema("ok", t("config.creato", percorso=percorso)))
    return OK


LIVELLI = {tm.NESSUNO: "nessuno", tm.BASE16: "16", tm.COLORI256: "256", tm.TRUECOLOR: "truecolor"}


def _tema(ctx: Contesto, args: Argomenti) -> int:
    tema = ctx.tema
    if args.json:
        ctx.stampa_json({"nome": tm.NOME, "colori": LIVELLI[tema.livello], "unicode": tema.unicode,
                         "palette": dict(tm.PALETTE)})
        return OK
    ctx.scrivi(tema("accento", t("tema.titolo", nome=tm.NOME)))
    ctx.scrivi("  " + t("tema.livello", colori=t("tema.colori." + LIVELLI[tema.livello]),
                        simboli="Unicode" if tema.unicode else "ASCII"))
    for ruolo in tm.RUOLI_TESTO:
        ctx.scrivi(f"  {tema(ruolo, tema.simbolo('punto') + ' ' + t('tema.ruolo.' + ruolo).ljust(22))}"
                   f" {tema('tenue', tm.PALETTE[ruolo])}")
    return OK


# ---------------------------------------------------------------- la tabella

def crea_registro() -> Registro:
    return Registro([
        Comando(("aiuto",), "generale", _aiuto, liberi=2, segnaposto="comando", priorita=10),
        Comando(("versione",), "generale", _versione, flag=JSON, priorita=90, solo_terminale=True),
        Comando(("esci",), "generale", _esci, solo_interattivo=True, priorita=95),
        Comando(("stato",), "stato", _stato, flag=JSON, priorita=20),
        Comando(("dispositivi",), "stato", _dispositivi, flag=JSON, solo_interattivo=True, priorita=15),
        Comando(("avvisi",), "stato", _avvisi, flag=JSON, solo_interattivo=True, priorita=15),
        Comando(("ascolta",), "ascolto", _ascolta, solo_terminale=True, priorita=30),
        # i comandi di sistema restano per la diagnosi da terminale, ma non affollano il cockpit
        Comando(("verifica",), "diagnostica", _verifica, flag=JSON, priorita=40, solo_terminale=True),
        Comando(("interfacce",), "diagnostica", _interfacce, flag=JSON, priorita=45, solo_terminale=True),
        Comando(("config",), "impostazioni", _config, flag=JSON, priorita=60, solo_terminale=True),
        Comando(("config", "crea"), "impostazioni", _config_crea, priorita=70, solo_terminale=True),
        Comando(("tema",), "impostazioni", _tema, flag=JSON, priorita=65, solo_terminale=True),
    ])
