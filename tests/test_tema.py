import io
import unittest

from strinix.ui import tema as tm
from strinix.ui.testo import adatta, avvolgi, componi, larghezza, larghezza_riga, taglia


class FintoTerminale(io.StringIO):
    def __init__(self, tty=True, encoding="utf-8"):
        super().__init__()
        self._tty, self._enc = tty, encoding

    def isatty(self):
        return self._tty

    @property
    def encoding(self):
        return self._enc


class TestLivelli(unittest.TestCase):
    def livello(self, env, tty=True, richiesti=True):
        return tm.livello_colori(FintoTerminale(tty), env, richiesti)

    def test_terminale_moderno(self):
        self.assertEqual(self.livello({"TERM": "xterm-256color", "COLORTERM": "truecolor"}), tm.TRUECOLOR)
        self.assertEqual(self.livello({"TERM": "xterm-256color"}), tm.COLORI256)
        self.assertEqual(self.livello({"TERM": "xterm"}), tm.BASE16)

    def test_no_color_e_dumb(self):
        self.assertEqual(self.livello({"TERM": "xterm-256color", "NO_COLOR": "1"}), tm.NESSUNO)
        self.assertEqual(self.livello({"TERM": "dumb"}), tm.NESSUNO)
        self.assertEqual(self.livello({"TERM": "xterm"}, tty=False), tm.NESSUNO)

    def test_forzature(self):
        self.assertEqual(self.livello({"TERM": "xterm", "STRINIX_COLOR": "never"}), tm.NESSUNO)
        self.assertEqual(self.livello({"TERM": "xterm", "NO_COLOR": "1", "STRINIX_COLOR": "always"}, tty=False),
                         tm.BASE16)
        self.assertEqual(self.livello({"TERM": "xterm", "STRINIX_COLOR": "always"}, richiesti=False), tm.NESSUNO)

    def test_unicode_o_ascii(self):
        self.assertTrue(tm.supporta_unicode(FintoTerminale(encoding="UTF-8")))
        self.assertFalse(tm.supporta_unicode(FintoTerminale(encoding="ascii")))
        self.assertEqual(tm.Tema(0, False).simbolo("gufo"), "oo")
        self.assertEqual(set(tm.SIMBOLI_ASCII), set(tm.SIMBOLI_UNICODE))
        self.assertTrue(all(c.isascii() for c in tm.SIMBOLI_ASCII.values()))


class TestColori(unittest.TestCase):
    def test_ruoli_per_livello(self):
        self.assertEqual(tm.Tema(tm.NESSUNO)("ok", "x"), "x")
        self.assertIn("\x1b[32m", tm.Tema(tm.BASE16)("ok", "x"))
        self.assertIn("38;5;", tm.Tema(tm.COLORI256)("ok", "x"))
        self.assertIn("38;2;146;180;138", tm.Tema(tm.TRUECOLOR)("ok", "x"))

    def test_sfondo_solo_da_256(self):
        self.assertEqual(tm.Tema(tm.BASE16).sgr_sfondo("sfondo"), "")
        self.assertIn("48;2;25;28;33", tm.Tema(tm.TRUECOLOR).sgr_sfondo("sfondo"))

    def test_256_nel_range(self):
        for colore in tm.PALETTE.values():
            self.assertTrue(16 <= tm.rgb_a_256(*tm._rgb(colore)) <= 255)
        self.assertEqual(tm.rgb_a_256(0, 0, 0), 16)
        self.assertEqual(tm.rgb_a_256(255, 255, 255), 231)


class TestTesto(unittest.TestCase):
    def test_larghezze(self):
        self.assertEqual(larghezza("abc"), 3)
        self.assertEqual(larghezza("日本"), 4)
        self.assertEqual(larghezza("é"), 1)

    def test_taglia(self):
        self.assertEqual(taglia("abcdef", 4), "abc…")
        self.assertLessEqual(larghezza(taglia("日本語テキスト", 5)), 5)
        self.assertEqual(taglia("abc", 0), "")

    def test_adatta_esatta(self):
        for colonne in (1, 5, 40):
            riga = adatta([("testo", "x" * 100), ("dati", "日本" * 30)], colonne)
            self.assertEqual(larghezza_riga(riga), colonne)

    def test_avvolgi(self):
        riga = [("tenue", "15:07:29 "), ("avviso", "L'indirizzo 192.168.1.254 prima era di 60:d8:a4:25:bb:5b, ora lo "
                                                   "dichiara de:ad:be:ef:00:01. Può essere normale, ma è anche il segno")]
        for colonne in (20, 33, 50, 80):
            righe = avvolgi(riga, colonne, rientro=9)
            with self.subTest(colonne=colonne):
                for r in righe:
                    self.assertLessEqual(larghezza_riga(r), colonne + 1)
                for r in righe[1:]:
                    testo = "".join(x for _, x in r)
                    self.assertTrue(testo.startswith(" " * 9) and testo[9] != " ", repr(testo))
        lunga = avvolgi([("testo", "x" * 100)], 30, rientro=2)
        self.assertGreater(len(lunga), 3)
        self.assertEqual(avvolgi([("testo", "corta")], 30), [[("testo", "corta")]])

    def test_componi_ripulisce(self):
        s = componi([("testo", "ciao\x1b[2J\x1b]0;x\x07")], tm.Tema(tm.NESSUNO), 30)
        self.assertNotIn("\x1b", s)
        self.assertEqual(len(s), 30)


if __name__ == "__main__":
    unittest.main()
