"""Il tema "Bosco notturno" e i livelli di colore del terminale.

I comandi non scrivono mai colori a mano: chiedono un *ruolo* (ok, avviso,
errore, tenue, accento, dati...) e il tema decide come mostrarlo in base a
quello che il terminale sa fare: niente colori, 16, 256 o milioni (truecolor).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

NOME = "Bosco notturno"

PALETTE = {
    "sfondo": "#191C21",      # notte
    "superficie": "#20242B",
    "bordo": "#2E333C",
    "selezione": "#2A2F37",
    "testo": "#D9D3C7",       # piuma
    "tenue": "#7C8491",       # grigio piuma
    "accento": "#D6A75B",     # ambra, gli occhi del gufo: dove puoi interagire
    "dati": "#8FB3C9",        # luna: MAC, IP e altri dati tecnici
    "ok": "#92B48A",          # muschio
    "avviso": "#DE9A62",      # foglia secca
    "errore": "#CF6F6F",      # bacca
}
RUOLI_TESTO = ("testo", "tenue", "accento", "dati", "ok", "avviso", "errore", "bordo")

# a 16 colori: stesso significato, colori standard del terminale
ANSI16 = {"testo": "", "tenue": "90", "accento": "33", "dati": "36", "ok": "32",
          "avviso": "93", "errore": "31", "bordo": "90"}

NESSUNO, BASE16, COLORI256, TRUECOLOR = 0, 16, 256, 16_777_216

SIMBOLI_UNICODE = {"ok": "✔", "avviso": "!", "errore": "✘", "punto": "●", "vuoto": "○", "gufo": "◉◉",
                   "freccia": "›", "pausa": "⏸", "cursore": "▌", "o": "─", "v": "│", "as": "┌", "ad": "┐",
                   "bs": "└", "bd": "┘", "puntini": "…"}
SIMBOLI_ASCII = {"ok": "+", "avviso": "!", "errore": "x", "punto": "*", "vuoto": "o", "gufo": "oo",
                 "freccia": ">", "pausa": "||", "cursore": ">", "o": "-", "v": "|", "as": "+", "ad": "+",
                 "bs": "+", "bd": "+", "puntini": "~"}


def _rgb(esadecimale: str) -> tuple[int, int, int]:
    h = esadecimale.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_a_256(r: int, g: int, b: int) -> int:
    """Il colore piu' vicino tra i 256 dello xterm (cubo 6x6x6 + scala di grigi)."""
    livelli = (0, 95, 135, 175, 215, 255)

    def vicino(v: int) -> int:
        return min(range(6), key=lambda i: abs(livelli[i] - v))

    ci = (vicino(r), vicino(g), vicino(b))
    cubo = tuple(livelli[i] for i in ci)
    grigio_i = max(0, min(23, round((((r + g + b) / 3) - 8) / 10)))
    grigio = 8 + grigio_i * 10

    def distanza(c: tuple[int, int, int]) -> int:
        return (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2

    if distanza((grigio, grigio, grigio)) < distanza(cubo):
        return 232 + grigio_i
    return 16 + 36 * ci[0] + 6 * ci[1] + ci[2]


def livello_colori(stream, env: Mapping[str, str] | None = None, richiesti: bool = True) -> int:
    """Quanti colori usare. --no-color vince su tutto; STRINIX_COLOR=always|never forza; NO_COLOR spegne."""
    env = os.environ if env is None else env
    if not richiesti:
        return NESSUNO
    forza = env.get("STRINIX_COLOR", "auto").lower()
    if forza == "never":
        return NESSUNO
    if forza != "always":
        if env.get("NO_COLOR"):
            return NESSUNO
        if not getattr(stream, "isatty", lambda: False)():
            return NESSUNO
        if env.get("TERM", "dumb") in ("", "dumb"):
            return NESSUNO
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return TRUECOLOR
    if "256color" in env.get("TERM", ""):
        return COLORI256
    return BASE16


def supporta_unicode(stream) -> bool:
    codifica = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    return codifica in ("utf8", "utf8mb4")


class Tema:
    def __init__(self, livello: int = NESSUNO, unicode: bool = True) -> None:
        self.livello = livello
        self.unicode = unicode

    @classmethod
    def per(cls, stream, richiesti: bool = True, env: Mapping[str, str] | None = None) -> Tema:
        return cls(livello_colori(stream, env, richiesti), supporta_unicode(stream))

    @property
    def colori(self) -> bool:
        return self.livello > NESSUNO

    def simbolo(self, nome: str) -> str:
        return (SIMBOLI_UNICODE if self.unicode else SIMBOLI_ASCII)[nome]

    def sgr_testo(self, ruolo: str) -> str:
        """Sequenza per il colore del testo (stringa vuota se niente colori)."""
        if self.livello == NESSUNO or ruolo not in PALETTE:
            return ""
        if self.livello == BASE16:
            codice = ANSI16.get(ruolo, "")
            return f"\x1b[{codice}m" if codice else "\x1b[39m"
        r, g, b = _rgb(PALETTE[ruolo])
        if self.livello == COLORI256:
            return f"\x1b[38;5;{rgb_a_256(r, g, b)}m"
        return f"\x1b[38;2;{r};{g};{b}m"

    def sgr_sfondo(self, ruolo: str) -> str:
        """Sfondo solo a 256 colori o piu': a 16 colori si lascia quello del terminale."""
        if self.livello < COLORI256 or ruolo not in PALETTE:
            return ""
        r, g, b = _rgb(PALETTE[ruolo])
        if self.livello == COLORI256:
            return f"\x1b[48;5;{rgb_a_256(r, g, b)}m"
        return f"\x1b[48;2;{r};{g};{b}m"

    def __call__(self, ruolo: str, testo: str) -> str:
        inizio = self.sgr_testo(ruolo)
        return f"{inizio}{testo}\x1b[0m" if inizio else testo
