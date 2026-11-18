"""La tabella chiusa dei comandi.

Ogni comando e' una voce fissa: parole, gruppo, descrizione, uso, flag ammessi,
quanti argomenti liberi accetta e quali valori. Riga di comando, console e cockpit
usano la stessa tabella. Nessun comando viene costruito da testo esterno e non si
lancia mai una shell: l'input non fidato non puo' diventare un comando.
"""

from __future__ import annotations

import difflib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TextIO

from strinix.messaggi import t
from strinix.ui.tema import Tema

GRUPPI = ("generale", "stato", "ascolto", "diagnostica", "impostazioni")
OK, FALLITO, USO = 0, 1, 2


@dataclass(frozen=True)
class Argomenti:
    flag: frozenset[str] = frozenset()
    liberi: tuple[str, ...] = ()

    @property
    def json(self) -> bool:
        return "--json" in self.flag


@dataclass
class Contesto:
    tema: Tema
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    sessione: Any = None          # la sessione di ascolto, se c'e' (cockpit)
    interattivo: bool = False     # True nel cockpit e nella console
    uscita: bool = False          # il comando `esci` chiede di chiudere cockpit o console

    def scrivi(self, testo: str = "") -> None:
        print(testo, file=self.out)

    def errore(self, chiave: str, /, **valori: object) -> None:
        print(f"{self.tema('errore', self.tema.simbolo('errore'))} {t(chiave, **valori)}", file=self.err)

    def stampa_json(self, dati: object) -> None:
        # json.dumps trasforma i caratteri di controllo in \uXXXX: niente escape nel terminale
        print(json.dumps(dati, ensure_ascii=False, indent=2, sort_keys=True), file=self.out)


Funzione = Callable[[Contesto, Argomenti], int]


@dataclass(frozen=True)
class Comando:
    parole: tuple[str, ...]
    gruppo: str
    funzione: Funzione
    flag: frozenset[str] = frozenset()
    liberi: int = 0                          # quanti argomenti liberi al massimo
    scelte: tuple[str, ...] | None = None    # valori ammessi per il primo argomento libero
    segnaposto: str = ""                     # come si chiama l'argomento libero nell'uso
    priorita: int = 50                       # piu' basso = proposto prima nel completamento
    solo_terminale: bool = False             # non ha senso dentro il cockpit (es. ascolta)
    solo_interattivo: bool = False           # ha senso solo con una sessione aperta
    nascosto: bool = False

    @property
    def nome(self) -> str:
        return " ".join(self.parole)

    @property
    def descrizione(self) -> str:
        return t("descr." + "_".join(self.parole))

    @property
    def uso(self) -> str:
        parti = [self.nome]
        if self.liberi:
            parti.append(f"[{self.segnaposto or '...'}]")
        parti += [f"[{f}]" for f in sorted(self.flag)]
        return " ".join(parti)


class Registro:
    def __init__(self, comandi: list[Comando]) -> None:
        self.comandi = {c.parole: c for c in comandi}
        if len(self.comandi) != len(comandi):
            raise ValueError("comando duplicato")
        for c in comandi:
            if c.gruppo not in GRUPPI:
                raise ValueError("gruppo sconosciuto")

    def visibili(self, interattivo: bool = False) -> list[Comando]:
        return [c for c in self.comandi.values()
                if not c.nascosto and not (interattivo and c.solo_terminale)
                and not (not interattivo and c.solo_interattivo)]

    def trova(self, args: list[str]) -> tuple[Comando | None, list[str]]:
        """Il comando con piu' parole che combacia con l'inizio di `args`."""
        for n in range(min(len(args), 3), 0, -1):
            c = self.comandi.get(tuple(args[:n]))
            if c is not None:
                return c, args[n:]
        return None, args

    def sottocomandi(self, parola: str) -> list[Comando]:
        return [c for c in self.comandi.values() if len(c.parole) > 1 and c.parole[0] == parola and not c.nascosto]

    def suggerisci(self, parola: str) -> str | None:
        nomi = sorted({c.parole[0] for c in self.comandi.values() if not c.nascosto})
        vicini = difflib.get_close_matches(parola.lower(), nomi, n=1, cutoff=0.6)
        return vicini[0] if vicini else None

    def completa(self, testo: str, interattivo: bool = True) -> list[Comando]:
        """Comandi il cui nome inizia con `testo`, i piu' utili per primi."""
        testo = " ".join(testo.split()).lower()
        trovati = [c for c in self.visibili(interattivo) if c.nome.startswith(testo)]
        return sorted(trovati, key=lambda c: (c.priorita, c.nome))

    def esegui(self, ctx: Contesto, args: list[str]) -> int:
        if not args:
            args = ["aiuto"]
        comando, resto = self.trova(args)
        if comando is None or comando.nascosto:
            sotto = self.sottocomandi(args[0])
            if sotto:
                ctx.errore("cli.sottocomandi", comando=args[0], scelte=", ".join(c.nome for c in sotto))
                return USO
            vicino = self.suggerisci(args[0])
            if vicino:
                ctx.errore("cli.sconosciuto_suggerimento", comando=args[0], suggerimento=vicino)
            else:
                ctx.errore("cli.sconosciuto", comando=args[0])
            return USO
        if not ctx.interattivo and comando.solo_interattivo:
            ctx.errore("cli.serve_cockpit")
            return FALLITO
        if ctx.interattivo and comando.solo_terminale:
            ctx.errore("cli.solo_terminale", comando=comando.nome)
            return USO
        if "--help" in resto or "-h" in resto:
            ctx.scrivi(t("cli.uso", uso=comando.uso))
            return OK
        flag, liberi = set(), []
        for a in resto:
            if a.startswith("-"):
                if a not in comando.flag:
                    ctx.errore("cli.flag_sconosciuto", flag=a, comando=comando.nome)
                    print(t("cli.uso", uso=comando.uso), file=ctx.err)
                    return USO
                flag.add(a)
            else:
                liberi.append(a)
        if len(liberi) > comando.liberi:
            sotto = self.sottocomandi(comando.parole[0]) if len(comando.parole) == 1 else []
            if sotto and not comando.liberi:
                ctx.errore("cli.sottocomando_sconosciuto", valore=liberi[0], comando=comando.nome,
                           scelte=", ".join(c.nome for c in sotto))
                return USO
            ctx.errore("cli.troppi_argomenti", comando=comando.nome)
            print(t("cli.uso", uso=comando.uso), file=ctx.err)
            return USO
        if comando.scelte is not None and liberi and liberi[0] not in comando.scelte:
            ctx.errore("cli.scelta_non_valida", valore=liberi[0], scelte=", ".join(comando.scelte))
            print(t("cli.uso", uso=comando.uso), file=ctx.err)
            return USO
        return comando.funzione(ctx, Argomenti(frozenset(flag), tuple(liberi)))
