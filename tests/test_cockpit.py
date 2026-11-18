import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import tempfile
import termios
import time
import unittest
from dataclasses import replace
from pathlib import Path

from strinix.comandi.integrati import crea_registro
from strinix.stato import Dispositivo, Evento
from strinix.tui.cockpit import PANNELLI, Cockpit
from strinix.tui.schermo import ENTRA, ESCI, tasti
from strinix.ui import tema as tm
from strinix.ui.testo import larghezza
from tests.aiuti import Finta

RADICE = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def visibile(riga):
    return ANSI.sub("", riga)


class FontaOstile(Finta):
    """Una fonte con dati costruiti da un attaccante: escape, testo lunghissimo, caratteri larghi."""

    def istantanea(self):
        ist = super().istantanea()
        brutto = Evento(time.time(), "info", "evento.dns",
                        {"ip": "192.168.1.66", "nome": "\x1b[2J\x1b]0;preso\x07" + "日本" * 50 + "\u202eevil"})
        return replace(ist, eventi=ist.eventi + (brutto,))


class FontaAffollata(Finta):
    """Una rete con tanti dispositivi e indirizzi lunghi, come quella vera di casa o dell'ITS."""

    def istantanea(self):
        ist = super().istantanea()
        extra = tuple(Dispositivo(f"e8:65:38:51:{i:02x}:c7", ist.inizio + i, ist.inizio + i, 3, f"192.168.50.{100 + i}")
                      for i in range(16))
        return replace(ist, dispositivi=ist.dispositivi + extra)


class TestRidisegno(unittest.TestCase):
    def test_solo_righe_cambiate(self):
        import io

        from strinix.tui.schermo import Schermo
        out = io.StringIO()
        s = Schermo(0, out)
        s.disegna(["a", "b", "c"])
        out.seek(0), out.truncate()
        s.disegna(["a", "B", "c"])
        self.assertEqual(out.getvalue(), "\x1b[2;1HB\x1b[0m")
        out.seek(0), out.truncate()
        s.disegna(["a", "B", "c"])
        self.assertEqual(out.getvalue(), "")


class TestTasti(unittest.TestCase):
    def test_niente_a_capo_automatico(self):
        self.assertIn("\x1b[?7l", ENTRA)
        self.assertIn("\x1b[?7h", ESCI)

    def test_sequenze(self):
        self.assertEqual(tasti("\x1b[A\x1b[B\x1b[Z\t\r"), ["su", "giu", "maiusc-tab", "tab", "invio"])
        self.assertEqual(tasti("\x1b[5~\x1b[6~\x1bOA"), ["pagsu", "paggiu", "su"])
        self.assertEqual(tasti("\x1b"), ["esc"])
        self.assertEqual(tasti("ab:"), ["a", "b", ":"])

    def test_sequenze_sconosciute_scartate(self):
        self.assertEqual(tasti("\x1b[999;999Rq"), ["q"])
        self.assertEqual(tasti("\x00\x07\x9b"), [])


class TestDisegno(unittest.TestCase):
    def cockpit(self, fonte=None, tema=None, **kw):
        return Cockpit(crea_registro(), tema or tm.Tema(tm.NESSUNO, True), fonte or Finta(), **kw)

    def controlla(self, c, colonne, righe):
        disegno = c.righe(colonne, righe)
        self.assertEqual(len(disegno), righe)
        for r in disegno:
            v = visibile(r)
            self.assertNotIn("\x1b", v)
            self.assertEqual(larghezza(v), colonne, repr(v))
        return disegno

    def test_ogni_dimensione_ogni_pannello(self):
        for tema in (tm.Tema(tm.NESSUNO, True), tm.Tema(tm.TRUECOLOR, True), tm.Tema(tm.BASE16, False)):
            c = self.cockpit(tema=tema)
            for colonne, righe in ((20, 8), (60, 15), (80, 24), (100, 30), (200, 60)):
                for n in range(len(PANNELLI)):
                    c.pannello = n
                    with self.subTest(colonne=colonne, righe=righe, pannello=PANNELLI[n]):
                        self.controlla(c, colonne, righe)

    def test_dati_ostili_non_escono(self):
        c = self.cockpit(FontaOstile())
        for colonne in (40, 120):
            testo = "".join(self.controlla(c, colonne, 30))
            self.assertNotIn("\x1b]", testo)
            self.assertNotIn("\x1b[2J", testo)
            self.assertNotIn("\u202e", testo)

    def test_rete_non_si_ripete(self):
        c = self.cockpit()
        for colonne in (100, 140, 220):
            for n, nome in enumerate(PANNELLI):
                c.pannello = n
                schermo = "".join(visibile(r) for r in c.righe(colonne, 40))
                with self.subTest(colonne=colonne, pannello=nome):
                    self.assertEqual(schermo.count("─ Rete "), 1)
                    self.assertLessEqual(schermo.count("─ Dispositivi"), 1)

    def test_colonna_di_destra_mai_tagliata(self):
        c = self.cockpit(FontaAffollata())
        for colonne in (100, 120, 160, 205, 260):
            for n in range(len(PANNELLI)):
                c.pannello = n
                for riga in c.righe(colonne, 40)[3:-2]:
                    destra = visibile(riga)[-max(40, min(56, colonne // 3)):]
                    with self.subTest(colonne=colonne, pannello=PANNELLI[n]):
                        self.assertNotIn("…", destra, destra)

    def test_pannello_principale_si_adatta(self):
        c = self.cockpit(FontaAffollata())
        c.pannello = 2
        for colonne in (60, 80, 100):
            testo = "".join(visibile(r) for r in c.righe(colonne, 40))
            with self.subTest(colonne=colonne):
                self.assertNotIn("…", testo.replace("puntini", ""))

    def test_ascii(self):
        c = self.cockpit(tema=tm.Tema(tm.NESSUNO, False))
        for r in c.righe(100, 20):
            self.assertTrue(r.isascii(), r)

    def test_benvenuto_prima_volta(self):
        c = self.cockpit(prima_volta=True)
        self.assertIn("Dove scrivo", "".join(c.righe(100, 30)))
        c.tasto("x")
        self.assertIsNone(c.sovrapposto)


class TestTastiCockpit(unittest.TestCase):
    def setUp(self):
        self.c = Cockpit(crea_registro(), tm.Tema(tm.NESSUNO, True), Finta())

    def scrivi(self, testo):
        for k in tasti(testo):
            self.c.tasto(k)

    def test_pannelli(self):
        self.scrivi("\t")
        self.assertEqual(PANNELLI[self.c.pannello], "dispositivi")
        self.scrivi("\x1b[Z\x1b[Z")
        self.assertEqual(PANNELLI[self.c.pannello], "avvisi")
        self.scrivi("4")
        self.assertEqual(PANNELLI[self.c.pannello], "chiaro")
        self.scrivi("5")
        self.assertEqual(PANNELLI[self.c.pannello], "rete")

    def test_comando_con_completamento(self):
        self.scrivi(":disp\t")
        self.assertEqual(self.c.buffer, "dispositivi ")
        self.scrivi("\r")
        titolo, righe = self.c.sovrapposto
        self.assertIn("16:37:f5:e1:63:98", "".join(t for r in righe for _, t in r))

    def test_comando_sbagliato_mostra_errore(self):
        self.scrivi(":verifca\r")
        titolo, righe = self.c.sovrapposto
        self.assertIn("verifica", "".join(t for r in righe for _, t in r))

    def test_ascolta_rifiutato_nel_cockpit(self):
        self.scrivi(":ascolta\r")
        self.assertIn("terminale", "".join(t for r in self.c.sovrapposto[1] for _, t in r))

    def test_esci_da_comando(self):
        self.scrivi(":esci\r")
        self.assertTrue(self.c.fine)

    def test_cronologia(self):
        self.scrivi(":stato\r\x1b:\x1b[A")
        self.assertEqual(self.c.buffer, "stato")

    def test_riga_comandi_limitata_e_solo_stampabili(self):
        self.scrivi(":" + "a" * 1000)
        self.assertLessEqual(len(self.c.buffer), 200)
        self.c.buffer = ""
        self.scrivi("\x00\x07")
        self.assertEqual(self.c.buffer, "")

    def test_cerca_filtra(self):
        self.scrivi("/github\r")
        voci = self.c.voci(self.c.istantanea(), "live")
        self.assertEqual(len(voci), 1)
        self.scrivi("\x1b")
        self.assertEqual(len(self.c.voci(self.c.istantanea(), "live")), 5)

    def test_intero_e_dettaglio(self):
        self.scrivi("3\x1b[A\r\r")
        titolo, _ = self.c.sovrapposto
        self.assertEqual(titolo, "Dispositivo")
        self.scrivi("\x1b\x1b")
        self.assertFalse(self.c.intero)

    def test_pausa_congela(self):
        self.scrivi("p")
        self.assertTrue(self.c.pausa)
        self.assertIs(self.c.istantanea(), self.c.istantanea())
        self.scrivi("p")
        self.assertFalse(self.c.pausa)

    def test_pannello_in_chiaro(self):
        self.scrivi("4")
        schermo = "".join(visibile(r) for r in self.c.righe(140, 30))
        self.assertIn("password in chiaro", schermo)
        self.assertIn("example.com", schermo)
        self.scrivi("\r\x1b[B\r")
        self.assertEqual(self.c.sovrapposto[0], "Traffico in chiaro")

    def test_copia_riga(self):
        self.scrivi("3c")
        self.assertIn("2c:8d:b1:71:af:ed", self.c.da_copiare)
        self.assertIn("Copiato", "".join(visibile(r) for r in self.c.righe(100, 20)))

    def test_copia_ripulita(self):
        self.c.fonte = FontaOstile()
        self.scrivi("2\x1b[Fc")
        self.assertNotIn("\x1b", self.c.da_copiare)
        self.assertNotIn("\u202e", self.c.da_copiare)

    def test_ordinamento(self):
        self.scrivi("3o")
        self.assertIn("per traffico", "".join(visibile(r) for r in self.c.righe(140, 30)))
        prima = self.c.voci(self.c.istantanea(), "dispositivi")[0][1]
        self.assertEqual(prima.mac, "2c:8d:b1:71:af:ed")   # quello con piu' traffico
        self.scrivi("ooo")
        self.assertEqual(self.c.ordine, 0)

    def test_dettaglio_dispositivo_vivo(self):
        self.scrivi("3\r\r")
        testo = "".join(x for r in self.c.sovrapposto[1] for _, x in r)
        for atteso in ("Intel", "github.com", "online", "inviati"):
            self.assertIn(atteso, testo)

    def test_comandi_di_sistema_fuori_dal_cockpit(self):
        nomi = [c.nome for c in self.c.registro.completa("")]
        for nome in ("verifica", "tema", "config", "interfacce", "versione"):
            self.assertNotIn(nome, nomi)
        self.scrivi(":aiuto\r")
        testo = "".join(x for r in self.c.sovrapposto[1] for _, x in r)
        self.assertNotIn("tema", testo)
        self.assertIn("dispositivi", testo)

    def test_q_esce(self):
        self.scrivi("q")
        self.assertTrue(self.c.fine)


PROGRAMMA = f"""
import sys
sys.path.insert(0, {str(RADICE)!r})
from tests.aiuti import Finta
from strinix.comandi.integrati import crea_registro
from strinix.tui import cockpit as ck
from strinix.ui.tema import Tema
c = ck.Cockpit(crea_registro(), Tema.per(sys.stdout), Finta())
try:
    sys.exit(ck.esegui(c, sys.stdin.fileno(), sys.stdout))
except SystemExit:
    from strinix.tui import schermo
    s = schermo.ultimo_segnale()
    if s is not None:
        print("segnale", s, file=sys.stderr)
    raise
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "serve un terminale Linux")
class TestTerminaleFinto(unittest.TestCase):
    """Il cockpit vero, in un terminale finto (pty): tasti veri, ripristino vero."""

    def avvia(self):
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update({"TERM": "xterm-256color", "XDG_STATE_HOME": tempfile.mkdtemp()})
            os.execv(sys.executable, [sys.executable, "-c", PROGRAMMA])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
        return pid, fd

    def leggi(self, fd, secondi=1.5):
        dati = b""
        fine = time.time() + secondi
        while time.time() < fine:
            pronti, _, _ = select.select([fd], [], [], 0.1)
            if pronti:
                try:
                    pezzo = os.read(fd, 65536)
                except OSError:
                    break
                if not pezzo:
                    break
                dati += pezzo
        return dati.decode("utf-8", "replace")

    def aspetta(self, pid):
        fine = time.time() + 5
        while time.time() < fine:
            p, stato = os.waitpid(pid, os.WNOHANG)
            if p:
                return stato
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        self.fail("il cockpit non si e' chiuso")

    def test_apre_naviga_e_ripristina(self):
        pid, fd = self.avvia()
        schermo = self.leggi(fd)
        self.assertIn(ENTRA[:8], schermo)
        self.assertIn("strinix", schermo)
        os.write(fd, b":stato\r")
        self.assertIn("Ascolto attivo", self.leggi(fd))
        os.write(fd, b"\x1b3c")
        self.assertIn("\x1b]52;c;", self.leggi(fd))      # copia negli appunti
        os.write(fd, b"\x1bq")
        resto = self.leggi(fd)
        self.assertTrue(os.WIFEXITED(self.aspetta(pid)))
        self.assertIn(ESCI, resto)
        os.close(fd)

    def test_ctrl_c_ripristina(self):
        pid, fd = self.avvia()
        self.leggi(fd)
        os.write(fd, b"\x03")
        resto = self.leggi(fd)
        stato = self.aspetta(pid)
        self.assertEqual(os.WEXITSTATUS(stato), 0)
        self.assertIn(ESCI, resto)
        os.close(fd)

    def test_segnale_ripristina_e_lo_dice(self):
        pid, fd = self.avvia()
        self.leggi(fd)
        os.kill(pid, signal.SIGTERM)
        resto = self.leggi(fd)
        stato = self.aspetta(pid)
        self.assertEqual(os.WEXITSTATUS(stato), 128 + signal.SIGTERM)
        self.assertIn(ESCI, resto)
        self.assertIn(f"segnale {int(signal.SIGTERM)}", resto)
        os.close(fd)

    def test_ridimensionamento(self):
        pid, fd = self.avvia()
        self.leggi(fd)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 20, 60, 0, 0))
        os.kill(pid, signal.SIGWINCH)
        self.assertIn("\x1b[2J", self.leggi(fd))
        os.write(fd, b"q")
        self.leggi(fd, 0.5)
        self.aspetta(pid)
        os.close(fd)


if __name__ == "__main__":
    unittest.main()
