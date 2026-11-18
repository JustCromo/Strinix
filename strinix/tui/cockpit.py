"""Il cockpit: la schermata a tutto terminale di Strinix.

E' solo una *vista*: legge un'istantanea dalla fonte (oggi la sessione di ascolto,
domani l'agente in background) e la disegna. Lo stato del cockpit (pannello,
selezione, riga dei comandi) e il disegno sono separati dal terminale, cosi'
si possono provare senza un terminale vero.
"""

from __future__ import annotations

import codecs
import io
import os
import select
import time

from strinix import cronologia
from strinix.comandi.integrati import testo_evento
from strinix.comandi.registro import Contesto, Registro
from strinix.messaggi import t
from strinix.sessione import Istantanea
from strinix.terminale import pulisci
from strinix.tui.schermo import Schermo, tasti
from strinix.ui import tema as tm
from strinix.ui.testo import Riga, adatta, componi, larghezza, larghezza_riga

PANNELLI = ("avvisi", "live", "dispositivi", "rete")
LATERALE = 34          # larghezza della colonna di destra sugli schermi larghi
SCHERMO_LARGO = 100
MAX_COMANDO = 200


def _ora(secondi: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(secondi))


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
        if prima_volta:
            self.apri_sovrapposto(t("cockpit.benvenuto.titolo"), self._righe_testo("cockpit.benvenuto.testo"))

    # ------------------------------------------------------------ dati

    def istantanea(self) -> Istantanea:
        if self.pausa and self._congelata is not None:
            return self._congelata
        return self.fonte.istantanea()

    def voci(self, ist: Istantanea, pannello: str) -> list[tuple[Riga, object]]:
        """Le righe del pannello (gia' filtrate) con l'oggetto a cui si riferiscono."""
        tema = self.tema
        voci: list[tuple[Riga, object]] = []
        if pannello in ("live", "avvisi"):
            eventi = ist.eventi if pannello == "live" else tuple(reversed(ist.avvisi))
            for e in eventi:
                grave = e.livello != "info"
                simbolo = tema.simbolo("avviso") if grave else tema.simbolo("punto")
                voci.append(([("tenue", _ora(e.ora) + " "), ("avviso" if grave else "ok", simbolo + " "),
                              ("avviso" if grave else "testo", testo_evento(e))], e))
        elif pannello == "dispositivi":
            for d in sorted(ist.dispositivi, key=lambda d: d.primo):
                tipo = ist.etichette.get(d.mac, "nessuna")
                voci.append(([("ok", tema.simbolo("punto") + " "), ("dati", d.mac + "  "),
                              ("testo", f"{(d.ip or '-'):<16}"), ("tenue", f"{t('tipo.' + tipo):<20}"),
                              ("tenue", _ora(d.primo))], d))
        else:
            for chiave, valore, ruolo in self._righe_rete(ist):
                voci.append(([("tenue", f"{t(chiave):<16}"), (ruolo, valore)], None))
        filtro = self.filtro[pannello].lower()
        if filtro:
            voci = [(r, o) for r, o in voci if filtro in "".join(x for _, x in r).lower()]
        return voci

    def _righe_rete(self, ist: Istantanea) -> list[tuple[str, str, str]]:
        i, s = ist.interfaccia, ist.strada
        stato = ("ok", t("rete.cattura_attiva")) if ist.attiva else ("errore", t("rete.cattura_ferma"))
        dns = {"no": ("ok", t("rete.dns_no")), "probabile": ("avviso", t("rete.dns_probabile")),
               "presto": ("tenue", t("rete.dns_presto"))}[ist.dns_cifrato]
        return [
            ("rete.interfaccia", f"{i.nome} ({t('interfacce.tipo.' + i.tipo)})" if i else "-", "testo"),
            ("rete.ip", (i.ipv4 or "-") if i else "-", "dati"),
            ("rete.mac", (i.mac or "-") if i else "-", "dati"),
            ("rete.router", s.gateway if s else "-", "dati"),
            ("rete.router_mac", ist.router_mac or "-", "dati"),
            ("rete.cattura", stato[1], stato[0]),
            ("rete.pacchetti", f"{ist.pacchetti} ({ist.pps:.1f}/s)", "testo"),
            ("rete.illeggibili", str(ist.illeggibili), "testo"),
            ("rete.dispositivi", str(len(ist.dispositivi)), "testo"),
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
        elif k in ("1", "2", "3", "4"):
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
                       sessione=self.fonte, interattivo=True)
        codice = self.registro.esegui(ctx, testo.split())
        if ctx.uscita:
            self.fine = True
            return
        righe = [[("testo", r)] for r in out.getvalue().rstrip("\n").split("\n") if out.getvalue()]
        righe += [[("errore", r)] for r in err.getvalue().rstrip("\n").split("\n") if err.getvalue()]
        titolo = pulisci(testo, 40) if codice == 0 else t("cockpit.risultato_errore", comando=pulisci(testo, 40))
        self.apri_sovrapposto(titolo, righe or [[("tenue", t("cockpit.nessun_risultato"))]])

    def _apri_dettaglio(self) -> None:
        nome = PANNELLI[self.pannello]
        voci = self.voci(self.istantanea(), nome)
        if not voci:
            return
        _, oggetto = voci[self._selezione_effettiva(nome, len(voci))]
        if oggetto is None:
            return
        if hasattr(oggetto, "chiave"):
            righe: list[Riga] = [[("testo", testo_evento(oggetto))], [("testo", "")],
                                 [("tenue", t("dettaglio.ora") + " "), ("testo", _ora(oggetto.ora))]]
            righe += [[("tenue", f"{k}: "), ("dati", str(v))] for k, v in oggetto.valori.items()]
            self.apri_sovrapposto(t("dettaglio.evento"), righe)
        else:
            ist = self.istantanea()
            d = oggetto
            righe = [
                [("tenue", f"{t('dettaglio.mac'):<22}"), ("dati", d.mac)],
                [("tenue", f"{t('dettaglio.ip'):<22}"), ("dati", d.ip or "-")],
                [("tenue", f"{t('dettaglio.tipo'):<22}"), ("testo", t("tipo." + ist.etichette.get(d.mac, "nessuna")))],
                [("tenue", f"{t('dettaglio.primo'):<22}"), ("testo", _ora(d.primo))],
                [("tenue", f"{t('dettaglio.ultimo'):<22}"), ("testo", _ora(d.ultimo))],
                [("tenue", f"{t('dettaglio.pacchetti'):<22}"), ("testo", str(d.pacchetti))],
            ]
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

    def _pannello(self, ist: Istantanea, nome: str, colonne: int, altezza: int, attivo: bool) -> list[Riga]:
        voci = self.voci(ist, nome)
        visibili = max(1, altezza - 2)
        sel = self._selezione_effettiva(nome, len(voci))
        inizio = max(0, min(sel - visibili + 1 if sel >= visibili else 0, max(0, len(voci) - visibili)))
        if sel < inizio:
            inizio = sel
        contenuto = [r for r, _ in voci[inizio:inizio + visibili]]
        if not voci:
            contenuto = [[("tenue", t("vuoto." + nome) if not self.filtro[nome] else t("vuoto.filtro"))]]
        titolo = t("pannello." + nome)
        if nome in ("dispositivi", "avvisi") and voci:
            titolo += f" · {len(voci)}"
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

    def _schede(self) -> Riga:
        riga: Riga = [("testo", " ")]
        for n, nome in enumerate(PANNELLI):
            etichetta = f"{n + 1} {t('pannello.' + nome)}"
            riga += [("accento", f"[{etichetta}]") if n == self.pannello else ("tenue", f" {etichetta} "), ("testo", "  ")]
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
        if ist.errore:
            messaggio: Riga = [("errore", " " + t(ist.errore[0], **ist.errore[1]))]
        elif self.filtro[PANNELLI[self.pannello]]:
            messaggio = [("tenue", " " + t("cockpit.filtro_attivo"))]
        else:
            messaggio = [("tenue", " " + t("cockpit.dove_scrivo"))]
        tasti_aiuto = "cockpit.tasti_sovrapposto" if self.sovrapposto else (
            "cockpit.tasti_intero" if self.intero else "cockpit.tasti")
        riga: Riga = [("testo", " ")]
        for coppia in t(tasti_aiuto).split("  "):
            tasto, _, cosa = coppia.partition(" ")
            riga += [("accento", tasto), ("tenue", f" {cosa}   ")]
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
            dx = max(LATERALE, min(50, colonne // 3))
            sx = colonne - dx - 1
            principale = self._pannello(ist, attivo, sx, alt_corpo, True)
            altro = "dispositivi" if attivo != "dispositivi" else "rete"
            alt_dx = alt_corpo // 2 if altro == "dispositivi" else alt_corpo
            laterale = self._pannello(ist, altro, dx, alt_dx, False)
            if altro == "dispositivi":
                laterale += self._pannello(ist, "rete", dx, alt_corpo - alt_dx, False)
            corpo = [principale[n] + [("testo", " ")] + laterale[n] for n in range(alt_corpo)]
        else:
            corpo = self._pannello(ist, attivo, colonne, alt_corpo, True)
        if self.sovrapposto is not None:
            corpo = self._sovrapponi(corpo, colonne)
        messaggio, piede = self._piede(colonne)
        tutte = [self._testata(ist, colonne), self._schede(), [("testo", "")], *corpo, messaggio, piede]
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
