"""Il cockpit: la schermata a tutto terminale di Strinix (stessa impostazione di Erinix).

Tre livelli, come in Erinix:
1. la panoramica: tutti i pannelli insieme, numerati, quello attivo con il bordo colorato;
2. Invio: il pannello attivo a tutto schermo;
3. Invio di nuovo: la pagina di dettaglio della riga scelta (cosa significa, cosa fare,
   cosa e' successo), con il testo che va a capo.

E' solo una *vista*: legge un'istantanea dalla fonte (oggi la sessione di ascolto,
domani l'agente in background) e la disegna. Lo sfondo non viene dipinto: si vede
quello del terminale, come in Erinix.
"""

from __future__ import annotations

import base64
import codecs
import io
import ipaddress
import os
import select
import time

from strinix import cronologia
from strinix.comandi.integrati import testo_evento
from strinix.comandi.registro import Contesto, Registro
from strinix.messaggi import t
from strinix.sessione import Istantanea
from strinix.stato import Dispositivo, Evento, InChiaro
from strinix.terminale import pulisci
from strinix.tui.schermo import Schermo, tasti
from strinix.ui import tema as tm
from strinix.ui.testo import Riga, adatta, avvolgi, componi, larghezza, larghezza_riga, taglia

PANNELLI = ("avvisi", "live", "dispositivi", "chiaro", "rete")
ORDINI = ("comparsa", "traffico", "nome", "ip")     # come si possono ordinare i dispositivi (tasto o)
SIMBOLO_LIVELLO = {"info": "punto", "avviso": "avviso", "grave": "errore"}
RUOLO_LIVELLO = {"info": "testo", "avviso": "avviso", "grave": "errore"}
SCHERMO_LARGO = 100   # da qui in su la panoramica a griglia; sotto, un pannello alla volta
ALTEZZA_MINIMA = 18
MAX_NOME_COLONNA = 28
NOTIFICA = 3.0        # secondi in cui resta visibile un messaggio come "copiato"
BLOCCHI = "▁▂▃▄▅▆▇█"
BLOCCHI_ASCII = "_.-=+*#@"


def _ora(secondi: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(secondi))


def _byte(n: float) -> str:
    for unita in ("B", "KB", "MB", "GB"):
        if n < 1024 or unita == "GB":
            return f"{n:.0f} {unita}" if unita == "B" else f"{n:.1f} {unita}"
        n /= 1024
    return f"{n:.1f} TB"


def _fa(secondi: float) -> str:
    """"adesso", "3 min fa", "2 h fa": quanto tempo fa, detto come lo direbbe una persona."""
    if secondi < 60:
        return t("tempo.adesso")
    if secondi < 3600:
        return t("tempo.minuti", n=int(secondi // 60))
    if secondi < 86400:
        return t("tempo.ore", n=int(secondi // 3600))
    return t("tempo.giorni", n=int(secondi // 86400))


def _chiave_ip(ip: str | None) -> tuple:
    try:
        a = ipaddress.ip_address(ip or "")
        return (a.version, int(a))
    except ValueError:
        return (9, 0)


def titolo_evento(e: Evento) -> str:
    """La frase breve che va nelle liste; il testo lungo sta nella pagina di dettaglio."""
    valori = dict(e.valori)
    if e.chiave == "evento.nuovo_dispositivo":
        valori["nome"] = valori.get("produttore") or t("titolo.un_dispositivo")
        valori["etichetta"] = t("etichetta." + valori.get("etichetta", "nessuna"))
    return t("titolo." + e.chiave.removeprefix("evento."), **valori)


def _rientro(riga: Riga) -> int:
    """Di quanto rientrano le righe che vanno a capo: spazi iniziali, piu' "1. " o "· " degli elenchi."""
    testo = "".join(x for _, x in riga)
    spazi = len(testo) - len(testo.lstrip(" "))
    resto = testo[spazi:]
    if len(resto) > 2 and resto[0].isdigit() and resto[1:3] == ". ":
        return spazi + 3
    if resto[:2] in ("· ", "- ", "• "):
        return spazi + 2
    if len(resto) > 9 and resto[2] == ":" and resto[5] == ":" and resto[8] == " ":   # "14:17:25 ..."
        return spazi + 9
    return spazi


class Cockpit:
    def __init__(self, registro: Registro, tema: tm.Tema, fonte, *, storia: list[str] | None = None,
                 prima_volta: bool = False) -> None:
        self.registro, self.tema, self.fonte = registro, tema, fonte
        self.pannello = 0
        self.selezione = {p: 0 for p in PANNELLI}
        self.segui_live = True
        self.filtro = {p: "" for p in PANNELLI}
        self.intero = False                    # il pannello attivo a tutto schermo
        self.pausa = False
        self._congelata: Istantanea | None = None
        self.modo = "normale"                  # "normale", "comando", "cerca"
        self.buffer = ""
        self.storia = list(storia or [])
        self._indice_storia = len(self.storia)
        self.sovrapposto: tuple[str, list[Riga]] | None = None   # la pagina aperta: titolo e righe
        self.scorri_sovr = 0
        self.fine = False
        self.ordine = 0
        self.da_copiare: str | None = None     # il ciclo lo manda al terminale (OSC 52)
        self.notifica: tuple[str, float] | None = None
        self._nascosti: set[str] = set()       # pannelli che non stanno nella panoramica
        if prima_volta:
            self.apri_sovrapposto(t("cockpit.benvenuto.titolo"), self._righe_testo("cockpit.benvenuto.testo"))

    # ------------------------------------------------------------ dati

    def istantanea(self) -> Istantanea:
        if self.pausa and self._congelata is not None:
            return self._congelata
        return self.fonte.istantanea()

    def voci(self, ist: Istantanea, pannello: str, spazio: int | None = None,
             completo: bool = False) -> list[tuple[Riga, object]]:
        """Le righe del pannello (gia' filtrate) con l'oggetto a cui si riferiscono.

        `spazio` sono le colonne disponibili: se e' poco si mostrano meno colonne invece
        di tagliare a meta'. `completo` e' il pannello a tutto schermo, con piu' dettagli.
        """
        voci: list[tuple[Riga, object]] = []
        if pannello == "live":
            for e in ist.eventi:
                ruolo = RUOLO_LIVELLO.get(e.livello, "testo")
                simbolo = self.tema.simbolo(SIMBOLO_LIVELLO.get(e.livello, "punto"))
                voci.append(([("tenue", _ora(e.ora) + "  "), (ruolo if e.livello != "info" else "ok", simbolo + " "),
                              (ruolo, titolo_evento(e))], e))
        elif pannello == "avvisi":
            adesso = time.time()
            for e in reversed(ist.avvisi):
                ruolo = RUOLO_LIVELLO.get(e.livello, "avviso")
                voci.append(([(ruolo, self.tema.simbolo(SIMBOLO_LIVELLO.get(e.livello, "avviso")) + " "),
                              ("testo", titolo_evento(e)), ("tenue", "  " + _fa(adesso - e.ora))], e))
        elif pannello == "dispositivi":
            voci = self._voci_dispositivi(ist, spazio, completo)
        elif pannello == "chiaro":
            voci = self._voci_chiaro(ist)
        else:
            righe = self._righe_rete(ist, breve=not completo)
            etichetta = max(larghezza(t(k)) for k, _, _ in righe) + 2
            for chiave, valore, ruolo in righe:
                voci.append(([("tenue", t(chiave).ljust(etichetta)), (ruolo, valore)], None))
            if completo:
                voci += [(r, None) for r in self._riepilogo(ist)]
        filtro = self.filtro[pannello].lower()
        if filtro:
            voci = [(r, o) for r, o in voci if filtro in "".join(x for _, x in r).lower()]
        return voci

    def nome_mostrato(self, ist: Istantanea, d: Dispositivo, breve: bool = False) -> str:
        """Il nome piu' utile per una persona: quello dichiarato, poi il ruolo, poi il produttore."""
        tipo = ist.etichette.get(d.mac, "nessuna")
        if d.nome:
            return d.nome
        if tipo in ("questo_computer", "router"):
            if breve:
                return t("tipo_breve." + tipo)
            return t("tipo." + tipo) + (f" · {d.produttore}" if d.produttore else "")
        if d.produttore:
            return d.produttore
        return t("tipo_breve.mac_casuale") if d.mac_casuale else t("dispositivi.sconosciuto")

    def _ordinati(self, ist: Istantanea) -> list[Dispositivo]:
        ordine = ORDINI[self.ordine]
        schede = list(ist.dispositivi)
        if ordine == "traffico":
            return sorted(schede, key=lambda d: -(d.byte_inviati + d.byte_ricevuti))
        if ordine == "nome":
            return sorted(schede, key=lambda d: self.nome_mostrato(ist, d).lower())
        if ordine == "ip":
            return sorted(schede, key=lambda d: _chiave_ip(d.ip))
        return sorted(schede, key=lambda d: d.primo)

    def _voci_dispositivi(self, ist: Istantanea, spazio: int | None, completo: bool) -> list[tuple[Riga, object]]:
        schede = self._ordinati(ist)
        if not schede:
            return []
        adesso = time.time()
        nomi = {d.mac: self.nome_mostrato(ist, d) for d in schede}
        su, giu = ("↑", "↓") if self.tema.unicode else ("^", "v")
        traffico = {d.mac: f"{su} {_byte(d.byte_inviati)}  {giu} {_byte(d.byte_ricevuti)}" for d in schede}
        ultima = {d.mac: _fa(adesso - d.ultimo) for d in schede}
        larghe = {
            "nome": min(MAX_NOME_COLONNA, max(larghezza(n) for n in nomi.values())),
            "ip": max(len(d.ip or "-") for d in schede),
            "mac": 17,
            "traffico": max(larghezza(x) for x in traffico.values()),
            "ultima": max(larghezza(x) for x in ultima.values()),
        }
        # le colonne entrano in ordine di importanza finche' c'e' posto; poi si mostrano in ordine di lettura
        priorita = ("nome", "ip", "ultima", "traffico", "mac")
        scelte, usato = [], 2
        for col in priorita:
            serve = larghe[col] + (2 if scelte else 0)
            if spazio is not None and usato + serve > spazio:
                if col == "nome" and spazio - usato >= 8:     # il nome si accorcia, non sparisce
                    larghe["nome"] = spazio - usato
                    scelte.append(col)
                    usato += larghe["nome"]
                continue
            scelte.append(col)
            usato += serve
        resto = (spazio - usato - 2) if spazio is not None else 0
        if completo and resto >= 20 and any(d.destinazioni for d in schede):
            scelte.append("dest")
            larghe["dest"] = resto
        voci = []
        for d in schede:
            tipo = ist.etichette.get(d.mac, "nessuna")
            vivo = d.online(adesso)
            ruolo_punto = ("accento" if tipo == "questo_computer" else "dati" if tipo == "router" else "ok") if vivo else "tenue"
            riga: Riga = [(ruolo_punto, self.tema.simbolo("punto" if vivo else "vuoto") + " ")]
            siti = ", ".join(x for x, _ in sorted(d.destinazioni.items(), key=lambda v: -v[1]))
            celle = {"nome": ("testo" if vivo else "tenue", taglia(nomi[d.mac], larghe["nome"]).ljust(larghe["nome"])),
                     "ip": ("testo", (d.ip or "-").ljust(larghe["ip"])),
                     "mac": ("dati", d.mac),
                     "traffico": ("tenue", traffico[d.mac].ljust(larghe["traffico"])),
                     "ultima": ("tenue", ultima[d.mac].ljust(larghe["ultima"])),
                     "dest": ("dati", taglia(siti, larghe.get("dest", 0)))}
            primo = True
            for col in ("nome", "ip", "mac", "traffico", "ultima", "dest"):
                if col in scelte:
                    riga.append((celle[col][0], ("" if primo else "  ") + celle[col][1]))
                    primo = False
            voci.append((riga, d))
        return voci

    def _voci_chiaro(self, ist: Istantanea) -> list[tuple[Riga, object]]:
        voci = []
        adesso = time.time()
        for c in sorted(ist.chiaro, key=lambda c: -c.ultimo):
            ruolo = "errore" if c.credenziali else "avviso"
            riga: Riga = [(ruolo, self.tema.simbolo(ruolo) + " "),
                          ("testo", t("chiaro.riga", chi=c.chi, protocollo=c.protocollo, dove=c.dove))]
            if c.credenziali:
                riga.append(("errore", "  " + t("chiaro.password")))
            riga.append(("tenue", "  " + _fa(adesso - c.ultimo) + (f"  ×{c.volte}" if c.volte > 1 else "")))
            voci.append((riga, c))
        return voci

    def _riepilogo(self, ist: Istantanea) -> list[Riga]:
        """Chi usa di piu' la rete e i siti piu' usati (nel pannello Rete a tutto schermo)."""
        righe: list[Riga] = []
        attivi = [d for d in sorted(ist.dispositivi, key=lambda d: -(d.byte_inviati + d.byte_ricevuti))
                  if d.byte_inviati + d.byte_ricevuti > 0][:8]
        if attivi:
            righe += [[("testo", "")], [("dati", t("riepilogo.piu_attivi"))]]
            larg = max(larghezza(self.nome_mostrato(ist, d)) for d in attivi) + 2
            for d in attivi:
                righe.append([("testo", self.nome_mostrato(ist, d).ljust(larg)),
                              ("tenue", _byte(d.byte_inviati + d.byte_ricevuti))])
        siti: dict[str, int] = {}
        for d in ist.dispositivi:
            for dove, volte in d.destinazioni.items():
                siti[dove] = siti.get(dove, 0) + volte
        if siti:
            righe += [[("testo", "")], [("dati", t("riepilogo.siti"))]]
            ordinati = sorted(siti.items(), key=lambda x: -x[1])[:10]
            larg = max(larghezza(x) for x, _ in ordinati) + 2
            for dove, volte in ordinati:
                righe.append([("testo", dove.ljust(larg)), ("tenue", f"×{volte}")])
        return righe

    def _righe_rete(self, ist: Istantanea, breve: bool = False) -> list[tuple[str, str, str]]:
        i, s = ist.interfaccia, ist.strada
        stato = ("ok", t("rete.cattura_attiva")) if ist.attiva else ("errore", t("rete.cattura_ferma"))
        suffisso = "_breve" if breve else ""
        ruolo_dns = {"no": "ok", "probabile": "avviso", "presto": "tenue"}[ist.dns_cifrato]
        dns = (ruolo_dns, t(f"rete.dns_{ist.dns_cifrato}{suffisso}"))
        adesso = time.time()
        online = sum(d.online(adesso) for d in ist.dispositivi)
        if ist.password_in_chiaro:
            chiaro = ("errore", t("rete.chiaro_password" + suffisso, numero=len(ist.chiaro), password=ist.password_in_chiaro))
        elif ist.chiaro:
            chiaro = ("avviso", t("rete.chiaro_si" + suffisso, numero=len(ist.chiaro)))
        else:
            chiaro = ("ok", t("rete.chiaro_no"))
        return [
            ("rete.interfaccia", f"{i.nome} ({t('interfacce.tipo.' + i.tipo)})" if i else "-", "testo"),
            ("rete.ip", (i.ipv4 or "-") if i else "-", "dati"),
            ("rete.mac", (i.mac or "-") if i else "-", "dati"),
            ("rete.router", s.gateway if s else "-", "dati"),
            ("rete.router_mac", ist.router_mac or "-", "dati"),
            ("rete.cattura", stato[1], stato[0]),
            ("rete.pacchetti", f"{ist.pacchetti} ({ist.pps:.1f}/s)", "testo"),
            ("rete.illeggibili", str(ist.illeggibili), "testo"),
            ("rete.dispositivi", t("rete.dispositivi_valore", totale=len(ist.dispositivi), online=online), "testo"),
            ("rete.in_chiaro", chiaro[1], chiaro[0]),
            ("rete.dns", dns[1], dns[0]),
        ]

    # ------------------------------------------------------------ pagine di dettaglio

    def apri_sovrapposto(self, titolo: str, righe: list[Riga]) -> None:
        self.sovrapposto = (titolo, righe)
        self.scorri_sovr = 0

    def _righe_testo(self, chiave: str, **valori: object) -> list[Riga]:
        return [[("testo", r)] for r in t(chiave, **valori).split("\n")]

    def _sezione(self, chiave: str) -> list[Riga]:
        return [[("testo", "")], [("accento", t(chiave))]]

    def _spiegazione(self, tipo: str, valori: dict) -> list[Riga]:
        """"Cosa significa" e "Cosa fare" per un tipo di evento, se esistono."""
        righe: list[Riga] = []
        significato = t(f"spiega.{tipo}.significato", **valori)
        if not significato.startswith("spiega."):
            righe += self._sezione("pagina.significato") + [[("testo", r)] for r in significato.split("\n")]
        fare = t(f"spiega.{tipo}.fare", **valori)
        if not fare.startswith("spiega."):
            righe += self._sezione("pagina.fare")
            righe += [[("testo", f"  {n}. {r}")] for n, r in enumerate(fare.split("\n"), 1)]
        return righe

    def _chiave_valore(self, coppie: list[tuple[str, str, str]]) -> list[Riga]:
        larg = max(larghezza(t(k)) for k, _, _ in coppie) + 3
        return [[("tenue", "  " + t(k).ljust(larg)), (ruolo, v)] for k, v, ruolo in coppie]

    def _eventi_di(self, ist: Istantanea, mac: str | None, ip: str | None) -> list[Riga]:
        legati = [e for e in ist.eventi if (mac and mac in (e.valori.get("mac"), e.valori.get("ora"), e.valori.get("prima")))
                  or (ip and ip in (e.valori.get("ip"), e.valori.get("ip_dove")))][-15:]
        if not legati:
            return []
        righe = self._sezione("pagina.successo")
        for e in legati:
            ruolo = RUOLO_LIVELLO.get(e.livello, "testo")
            righe.append([("tenue", "  " + _ora(e.ora) + "  "), (ruolo, titolo_evento(e))])
        return righe

    def _pagina_evento(self, e: Evento) -> None:
        ist = self.istantanea()
        tipo = e.chiave.removeprefix("evento.")
        valori = dict(e.valori)
        righe: list[Riga] = self._spiegazione(tipo, valori)[1:] or [[("testo", testo_evento(e))]]
        coppie = [("dettaglio.ora", f"{_ora(e.ora)} ({_fa(time.time() - e.ora)})", "testo")]
        coppie += [("campo." + k, str(v), "dati") for k, v in valori.items()
                   if v not in ("", None) and k in CAMPI_MOSTRATI]
        righe += self._sezione("pagina.dettagli") + self._chiave_valore(coppie)
        righe += self._eventi_di(ist, valori.get("mac"), valori.get("ip"))
        self.apri_sovrapposto(titolo_evento(e), righe)

    def _pagina_chiaro(self, c: InChiaro) -> None:
        tipo = "chiaro_password" if c.credenziali else "chiaro"
        valori = {"chi": c.chi, "protocollo": c.protocollo, "dove": c.dove}
        righe: list[Riga] = [[("errore" if c.credenziali else "avviso", t("titolo." + tipo, **valori))]]
        righe += self._spiegazione(tipo, valori)
        righe += self._sezione("pagina.dettagli") + self._chiave_valore([
            ("dettaglio.chi", c.chi, "testo"), ("dettaglio.protocollo", c.protocollo, "avviso"),
            ("dettaglio.dove", c.dove, "dati"), ("dettaglio.primo", _ora(c.primo), "testo"),
            ("dettaglio.ultimo", f"{_ora(c.ultimo)} ({_fa(time.time() - c.ultimo)})", "testo"),
            ("dettaglio.volte", str(c.volte), "testo")])
        self.apri_sovrapposto(t("dettaglio.chiaro"), righe)

    def _pagina_dispositivo(self, d: Dispositivo) -> None:
        ist = self.istantanea()
        adesso = time.time()
        vivo = d.online(adesso)
        righe: list[Riga] = [[("testo", self.nome_mostrato(ist, d)), ("tenue", f"  ·  {d.ip or '-'}  ·  "),
                              ("ok" if vivo else "tenue", t("dispositivi.online") if vivo else t("dispositivi.offline"))]]
        righe += self._sezione("pagina.scheda") + self._chiave_valore([
            ("dettaglio.nome", d.nome or t("dettaglio.nessun_nome"), "testo"),
            ("dettaglio.produttore", d.produttore or t("dispositivi.produttore_ignoto"), "testo"),
            ("dettaglio.ip", d.ip or "-", "dati"), ("dettaglio.mac", d.mac, "dati"),
            ("dettaglio.primo", _ora(d.primo), "testo"),
            ("dettaglio.ultimo", f"{_ora(d.ultimo)} ({_fa(adesso - d.ultimo)})", "testo"),
            ("dettaglio.traffico", f"{_byte(d.byte_inviati)} {t('dispositivi.inviati')} · "
                                   f"{_byte(d.byte_ricevuti)} {t('dispositivi.ricevuti')}", "testo"),
            ("dettaglio.pacchetti", str(d.pacchetti), "testo")])
        if d.destinazioni:
            righe += self._sezione("dettaglio.destinazioni")
            for dove, volte in sorted(d.destinazioni.items(), key=lambda x: -x[1])[:12]:
                righe.append([("testo", "  · " + dove), ("tenue", f"  ×{volte}")])
        if d.mac_casuale:
            righe += self._sezione("pagina.mac_casuale") + self._righe_testo("dettaglio.mac_casuale")
        righe += self._eventi_di(ist, d.mac, d.ip)
        self.apri_sovrapposto(t("dettaglio.dispositivo"), righe)

    # ------------------------------------------------------------ tasti

    def _sposta(self, passi: int) -> None:
        nome = PANNELLI[self.pannello]
        n = len(self.voci(self.istantanea(), nome))
        attuale = self._selezione_effettiva(nome, n)
        nuova = max(0, min(n - 1, attuale + passi)) if n else 0
        self.selezione[nome] = nuova
        if nome == "live":
            self.segui_live = n == 0 or nuova == n - 1

    def _selezione_effettiva(self, nome: str, n: int) -> int:
        if nome == "live" and self.segui_live:
            return max(0, n - 1)
        return max(0, min(self.selezione[nome], n - 1)) if n else 0

    def tasto(self, k: str) -> None:
        if self.modo in ("comando", "cerca"):
            self._tasto_riga(k)
            return
        if self.sovrapposto is not None:
            self._tasto_pagina(k)
            return
        if k in ("q", "ctrl-c", "ctrl-d"):
            if k == "q" and (self.intero or self.filtro[PANNELLI[self.pannello]]):
                self._indietro()
            else:
                self.fine = True
        elif k == "esc":
            self._indietro()
        elif k in ("tab", "destra"):
            self.pannello = (self.pannello + 1) % len(PANNELLI)
        elif k in ("maiusc-tab", "sinistra"):
            self.pannello = (self.pannello - 1) % len(PANNELLI)
        elif k in ("1", "2", "3", "4", "5"):
            self.pannello = int(k) - 1
        elif k in ("su", "k"):
            self._sposta(-1)
        elif k in ("giu", "j"):
            self._sposta(1)
        elif k == "pagsu":
            self._sposta(-10)
        elif k == "paggiu":
            self._sposta(10)
        elif k == "inizio":
            self._sposta(-10**9)
        elif k == "fine":
            self._sposta(10**9)
        elif k == "invio":          # come Erinix: prima a tutto schermo, poi il dettaglio della riga
            if not self.intero:
                self.intero = True
            else:
                self._apri_dettaglio()
        elif k == ":":
            self._apri_riga("comando")
        elif k == "/":
            self._apri_riga("cerca")
        elif k == "?":
            self.apri_sovrapposto(t("cockpit.guida.titolo"), self._righe_testo("cockpit.guida.testo"))
        elif k == "p":
            self.pausa = not self.pausa
            self._congelata = self.fonte.istantanea() if self.pausa else None
        elif k == "o" and PANNELLI[self.pannello] == "dispositivi":
            self.ordine = (self.ordine + 1) % len(ORDINI)
        elif k == "c":
            self._copia()

    def _tasto_pagina(self, k: str) -> None:
        if k in ("su", "k"):
            self.scorri_sovr = max(0, self.scorri_sovr - 1)
        elif k in ("giu", "j"):
            self.scorri_sovr += 1
        elif k == "pagsu":
            self.scorri_sovr = max(0, self.scorri_sovr - 10)
        elif k in ("paggiu", " "):
            self.scorri_sovr += 10
        elif k == ":":
            self.sovrapposto = None
            self._apri_riga("comando")
        elif k == "ctrl-c":
            self.fine = True
        else:
            self.sovrapposto = None

    def _indietro(self) -> None:
        nome = PANNELLI[self.pannello]
        if self.filtro[nome]:
            self.filtro[nome] = ""
        elif self.intero:
            self.intero = False

    def _apri_riga(self, modo: str) -> None:
        self.modo = modo
        self.buffer = self.filtro[PANNELLI[self.pannello]] if modo == "cerca" else ""
        self._indice_storia = len(self.storia)

    def _tasto_riga(self, k: str) -> None:
        if k in ("esc", "ctrl-c"):
            if self.modo == "cerca":
                self.filtro[PANNELLI[self.pannello]] = ""
            self.modo, self.buffer = "normale", ""
        elif k == "invio":
            testo, modo = self.buffer.strip(), self.modo
            self.modo, self.buffer = "normale", ""
            if modo == "cerca":
                self.filtro[PANNELLI[self.pannello]] = testo
                self.selezione[PANNELLI[self.pannello]] = 0
            elif testo:
                self.esegui(testo)
        elif k == "indietro":
            self.buffer = self.buffer[:-1]
        elif k == "ctrl-u":
            self.buffer = ""
        elif k == "tab" and self.modo == "comando":
            self._completa()
        elif k in ("su", "giu") and self.modo == "comando" and self.storia:
            passo = -1 if k == "su" else 1
            self._indice_storia = max(0, min(len(self.storia), self._indice_storia + passo))
            self.buffer = self.storia[self._indice_storia] if self._indice_storia < len(self.storia) else ""
        elif len(k) == 1 and k.isprintable() and len(self.buffer) < MAX_COMANDO:
            self.buffer += k
            if self.modo == "cerca":
                self.filtro[PANNELLI[self.pannello]] = self.buffer

    def _completa(self) -> None:
        trovati = self.registro.completa(self.buffer)
        if not trovati:
            return
        nomi = [c.nome for c in trovati]
        comune = os.path.commonprefix(nomi)
        if len(nomi) == 1:
            self.buffer = nomi[0] + " "
        elif len(comune) > len(self.buffer.strip()):
            self.buffer = comune

    def esegui(self, testo: str) -> None:
        """Esegue un comando della tabella e mostra il risultato in una pagina. Nessuna shell."""
        self.storia = cronologia.aggiungi(self.storia, testo)
        out, err = io.StringIO(), io.StringIO()
        ctx = Contesto(tema=tm.Tema(tm.NESSUNO, self.tema.unicode), out=out, err=err,
                       sessione=self.fonte, interattivo=True, nel_cockpit=True)
        codice = self.registro.esegui(ctx, testo.split())
        if ctx.uscita:
            self.fine = True
            return
        righe = [[("testo", r)] for r in out.getvalue().rstrip("\n").split("\n") if out.getvalue()]
        righe += [[("errore", r)] for r in err.getvalue().rstrip("\n").split("\n") if err.getvalue()]
        titolo = pulisci(testo, 40) if codice == 0 else t("cockpit.risultato_errore", comando=pulisci(testo, 40))
        self.apri_sovrapposto(titolo, righe or [[("tenue", t("cockpit.nessun_risultato"))]])

    def _scelto(self) -> object:
        nome = PANNELLI[self.pannello]
        voci = self.voci(self.istantanea(), nome)
        if not voci:
            return None
        return voci[self._selezione_effettiva(nome, len(voci))][1]

    def testo_da_copiare(self, oggetto: object) -> str | None:
        if isinstance(oggetto, Dispositivo):
            return " ".join(x for x in (self.nome_mostrato(self.istantanea(), oggetto), oggetto.ip, oggetto.mac) if x)
        if isinstance(oggetto, InChiaro):
            return t("chiaro.riga", chi=oggetto.chi, protocollo=oggetto.protocollo, dove=oggetto.dove)
        if isinstance(oggetto, Evento):
            return testo_evento(oggetto)
        return None

    def _copia(self) -> None:
        testo = self.testo_da_copiare(self._scelto())
        if testo:
            self.da_copiare = pulisci(testo, 1000)
            self.notifica = (t("cockpit.copiato"), time.monotonic())

    def _apri_dettaglio(self) -> None:
        oggetto = self._scelto()
        if isinstance(oggetto, Evento):
            self._pagina_evento(oggetto)
        elif isinstance(oggetto, InChiaro):
            self._pagina_chiaro(oggetto)
        elif isinstance(oggetto, Dispositivo):
            self._pagina_dispositivo(oggetto)

    # ------------------------------------------------------------ disegno

    def _riquadro(self, titolo: Riga, contenuto: list[Riga], colonne: int, altezza: int,
                  selezionata: int | None = None, attivo: bool = False) -> list[Riga]:
        s = self.tema.simbolo
        bordo = "bordo_attivo" if attivo else "bordo"
        interna = colonne - 2
        testa = [(bordo, s("as") + s("o") + " ")] + titolo + [("testo", " ")]
        testa = adatta(testa, colonne - 1, s("puntini"))
        testa[-1] = (bordo, s("o") * larghezza(testa[-1][1])) if testa[-1][1].strip() == "" else testa[-1]
        righe: list[Riga] = [testa + [(bordo, s("ad"))]]
        for n in range(altezza - 2):
            if n < len(contenuto):
                segno = [("accento", s("freccia"))] if n == selezionata else [("testo", " ")]
                corpo = adatta(segno + [("testo", " ")] + contenuto[n], interna, s("puntini"))
            else:
                corpo = [("testo", " " * interna)]
            righe.append([(bordo, s("v"))] + corpo + [(bordo, s("v"))])
        righe.append([(bordo, s("bs") + s("o") * interna + s("bd"))])
        return righe

    def _titolo(self, nome: str, n_voci: int, attivo: bool) -> Riga:
        riga: Riga = [("tenue", f"{PANNELLI.index(nome) + 1} "), ("bordo_attivo" if attivo else "testo", t("pannello." + nome))]
        if nome in ("avvisi", "dispositivi", "chiaro") and n_voci:
            riga.append(("tenue", f" {n_voci}"))
        if nome == "dispositivi" and ORDINI[self.ordine] != "comparsa":
            riga.append(("tenue", " · " + t("ordine." + ORDINI[self.ordine])))
        if self.filtro[nome]:
            riga.append(("avviso", " · " + t("cockpit.filtro", filtro=self.filtro[nome])))
        return riga

    def _pannello(self, ist: Istantanea, nome: str, colonne: int, altezza: int, attivo: bool,
                  completo: bool = False) -> list[Riga]:
        voci = self.voci(ist, nome, colonne - 4, completo)   # 4 = bordi, cursore e spazio
        visibili = max(1, altezza - 2)
        sel = self._selezione_effettiva(nome, len(voci))
        inizio = max(0, min(sel - visibili + 1 if sel >= visibili else 0, max(0, len(voci) - visibili)))
        if sel < inizio:
            inizio = sel
        contenuto = [r for r, _ in voci[inizio:inizio + visibili]]
        if not voci:
            contenuto = [[("tenue", t("vuoto." + nome) if not self.filtro[nome] else t("vuoto.filtro"))]]
        selezionata = (sel - inizio) if attivo and voci and nome != "rete" else None
        return self._riquadro(self._titolo(nome, len(voci), attivo), contenuto, colonne, altezza, selezionata, attivo)

    def _naturale(self, ist: Istantanea, nome: str) -> int:
        return max(1, len(self.voci(ist, nome))) + 2

    def _panoramica(self, ist: Istantanea, colonne: int, altezza: int) -> list[Riga]:
        """Tutti i pannelli insieme: a sinistra Avvisi e Live, a destra Dispositivi, In chiaro e Rete."""
        sx = colonne * 55 // 100
        dx = colonne - sx - 1
        attivo = PANNELLI[self.pannello]
        alt_avvisi = max(4, min(self._naturale(ist, "avvisi"), altezza * 2 // 5))
        sinistra = (self._pannello(ist, "avvisi", sx, alt_avvisi, attivo == "avvisi")
                    + self._pannello(ist, "live", sx, altezza - alt_avvisi, attivo == "live"))
        alt_rete = len(self._righe_rete(ist, breve=True)) + 2
        alt_chiaro = max(4, min(self._naturale(ist, "chiaro"), altezza // 4))
        destra_pezzi = [("dispositivi", 0), ("chiaro", alt_chiaro), ("rete", alt_rete)]
        self._nascosti = set()
        while sum(h for _, h in destra_pezzi) > altezza - 6 and len(destra_pezzi) > 1:   # finestra bassa
            nascosto, _ = destra_pezzi.pop(1 if len(destra_pezzi) > 2 else -1)
            self._nascosti.add(nascosto)
        fissi = sum(h for _, h in destra_pezzi)
        destra: list[Riga] = []
        for nome, h in destra_pezzi:
            destra += self._pannello(ist, nome, dx, h or (altezza - fissi), attivo == nome)
        return [sinistra[n] + [("testo", " ")] + destra[n] for n in range(altezza)]

    def _pagina(self, colonne: int, altezza: int) -> list[Riga]:
        """La pagina di dettaglio a tutto schermo: il testo va a capo e si scorre con le frecce."""
        assert self.sovrapposto is not None
        titolo, righe = self.sovrapposto
        interna = colonne - 4
        avvolte = [x for r in righe for x in avvolgi(r, interna, _rientro(r))]
        visibili = max(1, altezza - 2)
        self.scorri_sovr = max(0, min(self.scorri_sovr, max(0, len(avvolte) - visibili)))
        return self._riquadro([("bordo_attivo", titolo)], avvolte[self.scorri_sovr:], colonne, altezza, attivo=True)

    def _testata(self, ist: Istantanea, colonne: int) -> tuple[Riga, Riga]:
        """Due righe, come Erinix: una frase sulla situazione, poi i numeri e il grafico dell'attivita'."""
        tema = self.tema
        avvisi, password = len(ist.avvisi), ist.password_in_chiaro
        gravi = sum(e.livello == "grave" for e in ist.avvisi)
        if password:
            frase: Riga = [("errore", t("testata.password", numero=password))]
        elif gravi:
            frase = [("errore", t("testata.gravi", numero=gravi))]
        elif avvisi:
            frase = [("avviso", t("testata.avvisi", numero=avvisi))]
        else:
            frase = [("ok", t("testata.tranquillo"))]
        sinistra: Riga = [("accento", f"{tema.simbolo('gufo')} strinix"), ("tenue", " · ")] + frase
        nome = ist.interfaccia.nome if ist.interfaccia else "-"
        if self.pausa:
            destra: Riga = [("avviso", f"{tema.simbolo('pausa')} {t('cockpit.in_pausa')}")]
        elif ist.attiva:
            destra = [("tenue", t("cockpit.in_ascolto") + " "), ("ok", nome)]
        else:
            destra = [("errore", t("cockpit.cattura_ferma"))]
        prima = self._allinea(sinistra, destra, colonne)
        adesso = time.time()
        online = sum(d.online(adesso) for d in ist.dispositivi)
        numeri: Riga = [("testo", " "), ("testo", str(len(ist.dispositivi))), ("tenue", " " + t("testata.dispositivi") + "  "),
                        ("ok", str(online)), ("tenue", " " + t("testata.online") + "  "),
                        ("avviso" if avvisi else "tenue", str(avvisi)), ("tenue", " " + t("testata.n_avvisi") + "  "),
                        ("errore" if password else ("avviso" if ist.chiaro else "tenue"), str(len(ist.chiaro))),
                        ("tenue", " " + t("testata.in_chiaro") + "  "),
                        ("tenue", t("cockpit.router") + " "), ("dati", ist.strada.gateway if ist.strada else "-"),
                        ("tenue", f"  {ist.pps:.0f} pkt/s ")]
        spazio = colonne - larghezza_riga(numeri) - 1
        if spazio >= 12 and ist.storia_pps:
            numeri.append(("dati", self._grafico(ist.storia_pps, min(spazio, 60))))
        return prima, numeri

    def _grafico(self, valori: tuple[int, ...], colonne: int) -> str:
        """Un piccolo grafico a barre dell'attivita' (gli ultimi secondi a destra)."""
        valori = valori[-colonne:]
        massimo = max(valori) or 1
        blocchi = BLOCCHI if self.tema.unicode else BLOCCHI_ASCII
        return "".join(" " if v == 0 else blocchi[min(len(blocchi) - 1, v * len(blocchi) // (massimo + 1))]
                       for v in valori)

    def _allinea(self, sinistra: Riga, destra: Riga, colonne: int) -> Riga:
        spazio = colonne - larghezza_riga(sinistra) - larghezza_riga(destra) - 2
        if spazio < 1:
            return [("testo", " ")] + sinistra
        return [("testo", " ")] + sinistra + [("testo", " " * spazio)] + destra

    def _schede(self, colonne: int) -> Riga:
        """Le schede dei pannelli (a tutto schermo o su finestre strette), come in Erinix."""
        for compatte in (False, True):
            riga: Riga = [("testo", " ")]
            for n, nome in enumerate(PANNELLI):
                if n == self.pannello:
                    riga.append(("accento", f"{n + 1} {t('pannello.' + nome)}"))
                else:
                    riga.append(("tenue", f"{n + 1}" if compatte else f"{n + 1} {t('pannello.' + nome)}"))
                riga.append(("testo", "  " if compatte else "   "))
            if larghezza_riga(riga) <= colonne:
                return riga
        return riga

    def _piede(self, colonne: int) -> tuple[Riga, Riga]:
        """Le ultime due righe: un suggerimento e i tasti che servono adesso (oppure la riga dove scrivi)."""
        tema = self.tema
        if self.modo == "comando":
            comando, _ = self.registro.trova(self.buffer.split())
            trovati = [comando] if comando else self.registro.completa(self.buffer)
            if trovati:
                c = trovati[0]
                aiuto: Riga = [("tenue", " "), ("accento", c.uso), ("tenue", "  " + c.descrizione)]
            else:
                aiuto = [("tenue", " " + t("cockpit.nessun_comando"))]
            scrittura: Riga = [("accento", f" {tema.simbolo('freccia')} "), ("testo", self.buffer),
                               ("accento", tema.simbolo("cursore"))]
            return aiuto, scrittura
        if self.modo == "cerca":
            return ([("tenue", " " + t("cockpit.cerca_aiuto"))],
                    [("accento", " / "), ("testo", self.buffer), ("accento", tema.simbolo("cursore"))])
        ist = self.istantanea()
        if self.notifica and time.monotonic() - self.notifica[1] < NOTIFICA:
            messaggio: Riga = [("ok", " " + self.notifica[0])]
        elif ist.errore:
            messaggio = [("errore", " " + t(ist.errore[0], **ist.errore[1]))]
        elif self.filtro[PANNELLI[self.pannello]]:
            messaggio = [("tenue", " " + t("cockpit.filtro_attivo"))]
        else:
            messaggio = [("tenue", " " + t("cockpit.dove_scrivo"))]
        if self.sovrapposto:
            chiave = "cockpit.tasti_pagina"
        elif self.intero:
            chiave = "cockpit.tasti_intero_dispositivi" if PANNELLI[self.pannello] == "dispositivi" else "cockpit.tasti_intero"
        else:
            chiave = "cockpit.tasti"
        riga: Riga = [("testo", " ")]
        for n, coppia in enumerate(t(chiave).split("  ")):   # quando non c'e' posto si fermano, mai a meta'
            tasto, _, cosa = coppia.partition(" ")
            pezzi: Riga = [("testo", "   " if n else ""), ("accento", tasto), ("tenue", f" {cosa}")]
            if larghezza_riga(riga) + larghezza_riga(pezzi) > colonne:
                break
            riga += pezzi
        return messaggio, riga

    def righe(self, colonne: int, altezza: int) -> list[str]:
        """Disegna tutto lo schermo: una stringa per riga, ognuna larga esattamente `colonne`."""
        ist = self.istantanea()
        prima, numeri = self._testata(ist, colonne)
        panoramica = colonne >= SCHERMO_LARGO and altezza >= ALTEZZA_MINIMA and not self.intero and not self.sovrapposto
        vuota: Riga = [("testo", "")]     # un po' di respiro tra la testata e i pannelli
        testa = [prima, numeri, vuota] if panoramica else [prima, numeri, self._schede(colonne), vuota]
        alt_corpo = max(3, altezza - len(testa) - 2)
        if self.sovrapposto is not None:
            corpo = self._pagina(colonne, alt_corpo)
        elif panoramica:
            corpo = self._panoramica(ist, colonne, alt_corpo)
            if PANNELLI[self.pannello] in self._nascosti:   # il pannello scelto non ci sta: a tutto schermo
                self.intero = True
                return self.righe(colonne, altezza)
        else:
            corpo = self._pannello(ist, PANNELLI[self.pannello], colonne, alt_corpo, True, completo=True)
        messaggio, piede = self._piede(colonne)
        tutte = [*testa, *corpo, messaggio, piede]
        tutte = tutte[:altezza] + [[("testo", "")]] * max(0, altezza - len(tutte))
        return [componi(r, self.tema, colonne) for r in tutte]


MAX_COMANDO = 200
CAMPI_MOSTRATI = ("mac", "ip", "prima", "ora", "nome", "chi", "dove", "ip_dove", "servizio", "protocollo", "produttore",
                  "quanti", "porte", "secondi", "router", "vero_router", "lunghezza", "dominio", "volte")


def esegui(cockpit: Cockpit, fd_in: int, out) -> int:
    """Il ciclo del cockpit: legge i tasti, ridisegna ogni secondo o quando cambia qualcosa."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    with Schermo(fd_in, out) as schermo:
        ultimo = 0.0
        cambiato = True
        while not cockpit.fine:
            adesso = time.monotonic()
            if schermo.ridimensionato:
                schermo.pulisci()
                schermo.ridimensionato = False
                cambiato = True
            if cockpit.da_copiare:
                # OSC 52: chiede al terminale di mettere il testo negli appunti (testo gia' ripulito)
                dati_b64 = base64.b64encode(cockpit.da_copiare.encode("utf-8")).decode("ascii")
                out.write(f"\x1b]52;c;{dati_b64}\x07")
                cockpit.da_copiare = None
            if cambiato or adesso - ultimo >= 1.0:
                colonne, righe = schermo.dimensioni()
                schermo.disegna(cockpit.righe(colonne, righe))
                ultimo, cambiato = adesso, False
            pronti, _, _ = select.select([fd_in], [], [], 0.25)
            if pronti:
                dati = os.read(fd_in, 1024)
                if not dati:
                    break
                for k in tasti(decoder.decode(dati)):
                    cockpit.tasto(k)
                cambiato = True
    return 0
