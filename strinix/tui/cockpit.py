"""Il cockpit: la schermata a tutto terminale di Strinix.

E' solo una *vista*: legge un'istantanea dalla fonte (oggi la sessione di ascolto,
domani l'agente in background) e la disegna. Lo stato del cockpit (pannello,
selezione, riga dei comandi) e il disegno sono separati dal terminale, cosi'
si possono provare senza un terminale vero.
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
from strinix.ui.testo import Riga, adatta, componi, larghezza, larghezza_riga, taglia

PANNELLI = ("avvisi", "live", "dispositivi", "chiaro", "rete")
ORDINI = ("comparsa", "traffico", "nome", "ip")     # come si possono ordinare i dispositivi (tasto o)
RUOLO_LIVELLO = {"info": "testo", "avviso": "avviso", "grave": "errore"}
SIMBOLO_LIVELLO = {"info": "punto", "avviso": "avviso", "grave": "errore"}
MAX_NOME_COLONNA = 28
NOTIFICA = 3.0        # secondi in cui resta visibile un messaggio come "copiato"
LATERALE = 40          # larghezza minima della colonna di destra sugli schermi larghi
SCHERMO_LARGO = 100
MAX_COMANDO = 200


def _ora(secondi: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(secondi))


def _byte(n: int) -> str:
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


class Cockpit:
    def __init__(self, registro: Registro, tema: tm.Tema, fonte, *, storia: list[str] | None = None,
                 prima_volta: bool = False) -> None:
        self.registro, self.tema, self.fonte = registro, tema, fonte
        self.pannello = 1
        self.selezione = {p: 0 for p in PANNELLI}
        self.segui_live = True
        self.filtro = {p: "" for p in PANNELLI}
        self.intero = False
        self.pausa = False
        self._congelata: Istantanea | None = None
        self.modo = "normale"          # "normale", "comando", "cerca"
        self.buffer = ""
        self.storia = list(storia or [])
        self._indice_storia = len(self.storia)
        self.sovrapposto: tuple[str, list[Riga]] | None = None
        self.scorri_sovr = 0
        self.fine = False
        self.ordine = 0
        self.da_copiare: str | None = None              # il ciclo lo manda al terminale (OSC 52)
        self.notifica: tuple[str, float] | None = None
        if prima_volta:
            self.apri_sovrapposto(t("cockpit.benvenuto.titolo"), self._righe_testo("cockpit.benvenuto.testo"))

    # ------------------------------------------------------------ dati

    def istantanea(self) -> Istantanea:
        if self.pausa and self._congelata is not None:
            return self._congelata
        return self.fonte.istantanea()

    def voci(self, ist: Istantanea, pannello: str, spazio: int | None = None,
             breve: bool = False) -> list[tuple[Riga, object]]:
        """Le righe del pannello (gia' filtrate) con l'oggetto a cui si riferiscono.

        `spazio` sono le colonne disponibili: se e' poco le righe diventano compatte
        invece di essere tagliate a meta'. `breve` accorcia i valori (colonna di destra).
        """
        voci: list[tuple[Riga, object]] = []
        if pannello in ("live", "avvisi"):
            eventi = ist.eventi if pannello == "live" else tuple(reversed(ist.avvisi))
            for e in eventi:
                ruolo = RUOLO_LIVELLO.get(e.livello, "testo")
                simbolo = self.tema.simbolo(SIMBOLO_LIVELLO.get(e.livello, "punto"))
                voci.append(([("tenue", _ora(e.ora) + " "), (ruolo if e.livello != "info" else "ok", simbolo + " "),
                              (ruolo, testo_evento(e))], e))
        elif pannello == "dispositivi":
            voci = self._voci_dispositivi(ist, spazio, compatto=breve)
        elif pannello == "chiaro":
            voci = self._voci_chiaro(ist)
        else:
            righe = self._righe_rete(ist, breve=breve)
            etichetta = max(larghezza(t(k)) for k, _, _ in righe) + 2
            for chiave, valore, ruolo in righe:
                voci.append(([("tenue", t(chiave).ljust(etichetta)), (ruolo, valore)], None))
        filtro = self.filtro[pannello].lower()
        if filtro:
            voci = [(r, o) for r, o in voci if filtro in "".join(x for _, x in r).lower()]
        return voci

    def nome_mostrato(self, ist: Istantanea, d: Dispositivo) -> str:
        """Il nome piu' utile per una persona: quello dichiarato, poi il ruolo, poi il produttore."""
        tipo = ist.etichette.get(d.mac, "nessuna")
        if d.nome:
            return d.nome
        if tipo in ("questo_computer", "router"):
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

    def _voci_dispositivi(self, ist: Istantanea, spazio: int | None, compatto: bool) -> list[tuple[Riga, object]]:
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
        priorita = ("nome", "ip") if compatto else ("nome", "ip", "ultima", "traffico", "mac")
        scelte, usato = [], 2
        for col in priorita:
            serve = larghe[col] + (2 if scelte else 0)
            if spazio is not None and usato + serve > spazio:
                if col == "nome" and spazio - usato >= 8:     # il nome si accorcia, non sparisce
                    larghe["nome"] = spazio - usato - (larghe["ip"] + 2 if spazio - usato > larghe["ip"] + 10 else 0)
                    scelte.append(col)
                    usato += larghe["nome"]
                continue
            scelte.append(col)
            usato += serve
        voci = []
        for d in schede:
            tipo = ist.etichette.get(d.mac, "nessuna")
            vivo = d.online(adesso)
            ruolo_punto = ("accento" if tipo == "questo_computer" else "dati" if tipo == "router" else "ok") if vivo else "tenue"
            riga: Riga = [(ruolo_punto, self.tema.simbolo("punto" if vivo else "vuoto") + " ")]
            celle = {"nome": ("testo" if vivo else "tenue", taglia(nomi[d.mac], larghe["nome"]).ljust(larghe["nome"])),
                     "ip": ("testo", (d.ip or "-").ljust(larghe["ip"])),
                     "mac": ("dati", d.mac),
                     "traffico": ("tenue", traffico[d.mac].ljust(larghe["traffico"])),
                     "ultima": ("tenue", ultima[d.mac].ljust(larghe["ultima"]))}
            primo = True
            for col in ("nome", "ip", "mac", "traffico", "ultima"):
                if col in scelte:
                    riga.append((celle[col][0], ("" if primo else "  ") + celle[col][1]))
                    primo = False
            voci.append((riga, d))
        return voci

    def _voci_chiaro(self, ist: Istantanea) -> list[tuple[Riga, object]]:
        voci = []
        for c in sorted(ist.chiaro, key=lambda c: -c.ultimo):
            ruolo = "errore" if c.credenziali else "avviso"
            riga: Riga = [("tenue", _ora(c.ultimo) + " "), (ruolo, self.tema.simbolo(ruolo) + " "),
                          ("testo", t("chiaro.riga", chi=c.chi, protocollo=c.protocollo, dove=c.dove))]
            if c.credenziali:
                riga.append(("errore", "  " + t("chiaro.password")))
            if c.volte > 1:
                riga.append(("tenue", f"  ×{c.volte}"))
            voci.append((riga, c))
        return voci

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

    # ------------------------------------------------------------ tasti

    def apri_sovrapposto(self, titolo: str, righe: list[Riga]) -> None:
        self.sovrapposto = (titolo, righe)
        self.scorri_sovr = 0

    def _righe_testo(self, chiave: str) -> list[Riga]:
        return [[("testo", r)] for r in t(chiave).split("\n")]

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
            if k in ("su", "k"):
                self.scorri_sovr = max(0, self.scorri_sovr - 1)
            elif k in ("giu", "j"):
                self.scorri_sovr += 1
            elif k == "pagsu":
                self.scorri_sovr = max(0, self.scorri_sovr - 10)
            elif k == "paggiu":
                self.scorri_sovr += 10
            elif k == ":":
                self.sovrapposto = None
                self._apri_riga("comando")
            elif k == "ctrl-c":
                self.fine = True
            else:
                self.sovrapposto = None
            return
        if k in ("q", "ctrl-c", "ctrl-d"):
            if k == "q" and (self.intero or self.filtro[PANNELLI[self.pannello]]):
                self._indietro()
            else:
                self.fine = True
        elif k == "esc":
            self._indietro()
        elif k == "tab" or k == "destra":
            self.pannello = (self.pannello + 1) % len(PANNELLI)
        elif k in ("maiusc-tab", "sinistra"):
            self.pannello = (self.pannello - 1) % len(PANNELLI)
        elif k in ("1", "2", "3", "4", "5"):
            self.pannello = int(k) - 1
        elif k == "o" and PANNELLI[self.pannello] == "dispositivi":
            self.ordine = (self.ordine + 1) % len(ORDINI)
        elif k == "c":
            self._copia()
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
        elif k == "invio":
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
        """Esegue un comando della tabella e mostra il risultato in un riquadro. Nessuna shell."""
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
        if oggetto is None:
            return
        larg = 24
        if isinstance(oggetto, Evento):
            righe: list[Riga] = [[("testo", testo_evento(oggetto))], [("testo", "")],
                                 [("tenue", t("dettaglio.ora") + " "), ("testo", _ora(oggetto.ora))]]
            righe += [[("tenue", f"{k}: "), ("dati", str(v))] for k, v in oggetto.valori.items() if v not in ("", None)]
            self.apri_sovrapposto(t("dettaglio.evento"), righe)
        elif isinstance(oggetto, InChiaro):
            c = oggetto
            righe = [[("tenue", t("dettaglio." + k).ljust(larg)), (ruolo, v)] for k, v, ruolo in (
                ("chi", c.chi, "testo"), ("protocollo", c.protocollo, "avviso"), ("dove", c.dove, "dati"),
                ("primo", _ora(c.primo), "testo"), ("ultimo", _ora(c.ultimo), "testo"), ("volte", str(c.volte), "testo"))]
            righe += [[("testo", "")]] + [[("errore" if c.credenziali else "tenue", r)]
                                          for r in t("chiaro.spiega_password" if c.credenziali else "chiaro.spiega").split("\n")]
            self.apri_sovrapposto(t("dettaglio.chiaro"), righe)
        elif isinstance(oggetto, Dispositivo):
            ist = self.istantanea()
            d = oggetto
            adesso = time.time()
            campi = (
                ("nome", self.nome_mostrato(ist, d), "testo"),
                ("produttore", d.produttore or t("dispositivi.produttore_ignoto"), "testo"),
                ("ip", d.ip or "-", "dati"), ("mac", d.mac, "dati"),
                ("stato", t("dispositivi.online") if d.online(adesso) else t("dispositivi.offline"),
                 "ok" if d.online(adesso) else "tenue"),
                ("primo", _ora(d.primo), "testo"), ("ultimo", f"{_ora(d.ultimo)} ({_fa(adesso - d.ultimo)})", "testo"),
                ("traffico", f"{_byte(d.byte_inviati)} {t('dispositivi.inviati')} · "
                             f"{_byte(d.byte_ricevuti)} {t('dispositivi.ricevuti')}", "testo"),
                ("pacchetti", str(d.pacchetti), "testo"),
            )
            righe = [[("tenue", t("dettaglio." + k).ljust(larg)), (ruolo, v)] for k, v, ruolo in campi]
            if d.destinazioni:
                righe += [[("testo", "")], [("dati", t("dettaglio.destinazioni"))]]
                for dove, volte in sorted(d.destinazioni.items(), key=lambda x: -x[1])[:8]:
                    righe.append([("testo", "  " + dove), ("tenue", f"  ×{volte}")])
            if d.mac_casuale:
                righe += [[("testo", "")]] + [[("tenue", r)] for r in t("dettaglio.mac_casuale").split("\n")]
            self.apri_sovrapposto(t("dettaglio.dispositivo"), righe)

    # ------------------------------------------------------------ disegno

    def _riquadro(self, titolo: str, contenuto: list[Riga], colonne: int, altezza: int,
                  selezionata: int | None = None) -> list[Riga]:
        s = self.tema.simbolo
        interna = colonne - 2
        testa = f"{s('o')} {titolo} "
        righe: list[Riga] = [[("bordo", s("as")), ("dati", taglia_testa(testa, interna, s("o"))), ("bordo", s("ad"))]]
        for n in range(altezza - 2):
            if n < len(contenuto):
                segno = [("accento", s("cursore"))] if n == selezionata else [("testo", " ")]
                corpo = adatta(segno + [("testo", " ")] + contenuto[n], interna, s("puntini"))
            else:
                corpo = [("testo", " " * interna)]
            righe.append([("bordo", s("v"))] + corpo + [("bordo", s("v"))])
        righe.append([("bordo", s("bs") + s("o") * interna + s("bd"))])
        return righe

    def _pannello(self, ist: Istantanea, nome: str, colonne: int, altezza: int, attivo: bool,
                  compatto: bool = False) -> list[Riga]:
        voci = self.voci(ist, nome, colonne - 4, breve=compatto)   # 4 = bordi, cursore e spazio
        visibili = max(1, altezza - 2)
        sel = self._selezione_effettiva(nome, len(voci))
        inizio = max(0, min(sel - visibili + 1 if sel >= visibili else 0, max(0, len(voci) - visibili)))
        if sel < inizio:
            inizio = sel
        contenuto = [r for r, _ in voci[inizio:inizio + visibili]]
        if not voci:
            contenuto = [[("tenue", t("vuoto." + nome) if not self.filtro[nome] else t("vuoto.filtro"))]]
        titolo = t("pannello." + nome)
        if nome in ("dispositivi", "avvisi", "chiaro") and voci:
            titolo += f" · {len(voci)}"
        if nome == "dispositivi" and attivo and ORDINI[self.ordine] != "comparsa":
            titolo += " · " + t("ordine." + ORDINI[self.ordine])
        if self.filtro[nome]:
            titolo += " · " + t("cockpit.filtro", filtro=self.filtro[nome])
        selezionata = (sel - inizio) if attivo and voci and nome != "rete" else None
        return self._riquadro(titolo, contenuto, colonne, altezza, selezionata)

    def _testata(self, ist: Istantanea, colonne: int) -> Riga:
        tema = self.tema
        nome = ist.interfaccia.nome if ist.interfaccia else "-"
        sinistra: Riga = [("accento", f"{tema.simbolo('gufo')} strinix"), ("tenue", " · " + t("cockpit.in_ascolto") + " "),
                          ("testo", nome)]
        if self.pausa:
            stato: Riga = [("avviso", f"{tema.simbolo('pausa')} {t('cockpit.in_pausa')}")]
        elif ist.attiva:
            stato = [("ok", f"{tema.simbolo('punto')} {t('cockpit.cattura_attiva')}")]
        else:
            stato = [("errore", f"{tema.simbolo('vuoto')} {t('cockpit.cattura_ferma')}")]
        destra: Riga = [("tenue", t("cockpit.router") + " "), ("dati", ist.strada.gateway if ist.strada else "-"),
                        ("tenue", " · ")] + stato + [("tenue", f" · {ist.pps:.0f} pkt/s ")]
        spazio = colonne - larghezza_riga(sinistra) - larghezza_riga(destra) - 1
        if spazio < 1:  # finestra stretta: resta solo lo stato della cattura
            destra = stato + [("testo", " ")]
            spazio = colonne - larghezza_riga(sinistra) - larghezza_riga(destra) - 1
        if spazio < 1:
            return [("testo", " ")] + sinistra
        return [("testo", " ")] + sinistra + [("testo", " " * spazio)] + destra

    def _schede(self, colonne: int) -> Riga:
        """Le schede dei pannelli; se non ci stanno, solo i numeri e il nome di quella aperta."""
        for compatte in (False, True):
            riga: Riga = [("testo", " ")]
            for n, nome in enumerate(PANNELLI):
                if n == self.pannello:
                    riga.append(("accento", f"[{n + 1} {t('pannello.' + nome)}]"))
                else:
                    riga.append(("tenue", f" {n + 1} " if compatte else f" {n + 1} {t('pannello.' + nome)} "))
                riga.append(("testo", " " if compatte else "  "))
            if larghezza_riga(riga) <= colonne:
                return riga
        return riga

    def _piede(self, colonne: int) -> tuple[Riga, Riga]:
        """Le ultime due righe: suggerimento e tasti (oppure la riga dove scrivi)."""
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
        tasti_aiuto = "cockpit.tasti_sovrapposto" if self.sovrapposto else (
            "cockpit.tasti_intero" if self.intero else "cockpit.tasti")
        riga: Riga = [("testo", " ")]
        for n, coppia in enumerate(t(tasti_aiuto).split("  ")):   # quando non c'e' posto si fermano, mai a meta'
            tasto, _, cosa = coppia.partition(" ")
            pezzi: Riga = [("testo", "   " if n else ""), ("accento", tasto), ("tenue", f" {cosa}")]
            if larghezza_riga(riga) + larghezza_riga(pezzi) > colonne:
                break
            riga += pezzi
        return messaggio, riga

    def _sovrapponi(self, corpo: list[Riga], colonne: int) -> list[Riga]:
        assert self.sovrapposto is not None
        titolo, righe = self.sovrapposto
        larg = min(colonne - 4, 84)
        alt = min(len(corpo), len(righe) + 2)
        self.scorri_sovr = max(0, min(self.scorri_sovr, max(0, len(righe) - (alt - 2))))
        box = self._riquadro(titolo, righe[self.scorri_sovr:], larg, alt)
        sinistra = (colonne - larg) // 2
        for n, riga in enumerate(box):
            corpo[n] = [("testo", " " * sinistra)] + riga
        return corpo

    def righe(self, colonne: int, altezza: int) -> list[str]:
        """Disegna tutto lo schermo: una stringa per riga, ognuna larga esattamente `colonne`."""
        ist = self.istantanea()
        alt_corpo = max(3, altezza - 5)
        attivo = PANNELLI[self.pannello]
        if colonne >= SCHERMO_LARGO and not self.intero:
            dx = max(LATERALE, min(56, colonne // 3))
            sx = colonne - dx - 1
            principale = self._pannello(ist, attivo, sx, alt_corpo, True)
            # a destra solo i pannelli che non sono gia' aperti a sinistra
            laterali = [p for p in ("dispositivi", "rete") if p != attivo]
            if len(laterali) == 2:
                alt_dx = alt_corpo - 12 if alt_corpo >= 24 else alt_corpo // 2   # la Rete ha 10 righe fisse
                laterale = (self._pannello(ist, "dispositivi", dx, alt_dx, False, compatto=True)
                            + self._pannello(ist, "rete", dx, alt_corpo - alt_dx, False, compatto=True))
            else:
                laterale = self._pannello(ist, laterali[0], dx, alt_corpo, False, compatto=True)
            corpo = [principale[n] + [("testo", " ")] + laterale[n] for n in range(alt_corpo)]
        else:
            corpo = self._pannello(ist, attivo, colonne, alt_corpo, True)
        if self.sovrapposto is not None:
            corpo = self._sovrapponi(corpo, colonne)
        messaggio, piede = self._piede(colonne)
        tutte = [self._testata(ist, colonne), self._schede(colonne), [("testo", "")], *corpo, messaggio, piede]
        tutte = tutte[:altezza] + [[("testo", "")]] * max(0, altezza - len(tutte))
        return [componi(r, self.tema, colonne, "sfondo") for r in tutte]


def taglia_testa(testo: str, spazio: int, riempi: str) -> str:
    if larghezza(testo) >= spazio:
        return testo[:spazio]
    return testo + riempi * (spazio - larghezza(testo))


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
