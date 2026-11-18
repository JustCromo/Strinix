"""Il terminale in modalita' cockpit: schermo alternativo, tasti grezzi, ripristino garantito.

Qualunque cosa succeda (uscita normale, Ctrl+C, errore, finestra chiusa, kill) il
terminale torna com'era. Se Strinix viene fermato da un segnale, lo dice in una riga.
"""

from __future__ import annotations

import atexit
import os
import signal
import termios
import tty

ENTRA = "\x1b[?1049h\x1b[?25l\x1b[2J"     # schermo alternativo, cursore nascosto, pulisci
ESCI = "\x1b[0m\x1b[?25h\x1b[?1049l"      # colori normali, cursore visibile, schermo normale

_ultimo_segnale: int | None = None


def ultimo_segnale() -> int | None:
    return _ultimo_segnale


TASTI_SPECIALI = {
    "\x1b[A": "su", "\x1b[B": "giu", "\x1b[C": "destra", "\x1b[D": "sinistra",
    "\x1bOA": "su", "\x1bOB": "giu", "\x1bOC": "destra", "\x1bOD": "sinistra",
    "\x1b[5~": "pagsu", "\x1b[6~": "paggiu", "\x1b[Z": "maiusc-tab",
    "\x1b[H": "inizio", "\x1b[F": "fine", "\x1bOH": "inizio", "\x1bOF": "fine",
    "\x1b[1~": "inizio", "\x1b[4~": "fine", "\x1b[7~": "inizio", "\x1b[8~": "fine", "\x1b[3~": "canc",
}
CONTROLLI = {"\t": "tab", "\r": "invio", "\n": "invio", "\x7f": "indietro", "\x08": "indietro",
             "\x03": "ctrl-c", "\x04": "ctrl-d", "\x15": "ctrl-u", "\x0c": "ctrl-l", "\x1b": "esc"}


def tasti(testo: str) -> list[str]:
    """Trasforma i caratteri letti dal terminale in nomi di tasti. Le sequenze sconosciute vengono scartate."""
    fuori: list[str] = []
    i = 0
    while i < len(testo):
        c = testo[i]
        if c == "\x1b" and i + 1 < len(testo) and testo[i + 1] in "[O":
            j = i + 2
            if testo[i + 1] == "[":
                while j < len(testo) and not ("\x40" <= testo[j] <= "\x7e"):
                    j += 1
            sequenza = testo[i:j + 1]
            nome = TASTI_SPECIALI.get(sequenza)
            if nome:
                fuori.append(nome)
            i = j + 1
            continue
        if c in CONTROLLI:
            fuori.append(CONTROLLI[c])
        elif c.isprintable():
            fuori.append(c)
        i += 1
    return fuori


class Schermo:
    """`with Schermo(fd_in, out) as s:` entra in modalita' cockpit e garantisce l'uscita."""

    def __init__(self, fd_in: int, out) -> None:
        self.fd_in, self.out = fd_in, out
        self._salvato = None
        self._gestori: dict[int, object] = {}
        self.ridimensionato = True
        self._attivo = False

    def dimensioni(self) -> tuple[int, int]:
        try:
            d = os.get_terminal_size(self.out.fileno())
        except (OSError, ValueError):
            return 80, 24
        if d.columns <= 0 or d.lines <= 0:  # terminale che non dichiara le dimensioni
            return 80, 24
        return max(d.columns, 20), max(d.lines, 8)

    def _al_segnale(self, numero: int, _frame) -> None:
        global _ultimo_segnale
        if numero == signal.SIGWINCH:
            self.ridimensionato = True
            return
        _ultimo_segnale = numero
        self.ripristina()
        raise SystemExit(128 + numero)

    def __enter__(self) -> Schermo:
        self._salvato = termios.tcgetattr(self.fd_in)
        atexit.register(self.ripristina)
        for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGWINCH):
            self._gestori[s] = signal.signal(s, self._al_segnale)
        tty.setraw(self.fd_in)
        self._attivo = True
        self.out.write(ENTRA)
        self.out.flush()
        return self

    def ripristina(self) -> None:
        if not self._attivo:
            return
        self._attivo = False
        try:
            self.out.write(ESCI)
            self.out.flush()
        except (OSError, ValueError):
            pass
        if self._salvato is not None:
            try:
                termios.tcsetattr(self.fd_in, termios.TCSAFLUSH, self._salvato)
            except termios.error:
                pass

    def __exit__(self, *exc: object) -> None:
        self.ripristina()
        for s, vecchio in self._gestori.items():
            signal.signal(s, vecchio)
        atexit.unregister(self.ripristina)

    def disegna(self, righe: list[str]) -> None:
        """Ridisegna tutto lo schermo in un colpo solo (niente sfarfallio)."""
        parti = ["\x1b[H"]
        for n, riga in enumerate(righe):
            parti.append(f"\x1b[{n + 1};1H{riga}\x1b[0m")
        self.out.write("".join(parti))
        self.out.flush()

    def pulisci(self) -> None:
        self.out.write("\x1b[0m\x1b[2J")
        self.out.flush()
