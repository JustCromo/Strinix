import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from strinix import __version__
from strinix.cli import main


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self._tmp.name, "XDG_STATE_HOME": self._tmp.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def esegui(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            codice = main(list(args), out=out)
        return codice, out.getvalue(), err.getvalue()

    def test_aiuto_senza_argomenti(self):
        codice, out, _ = self.esegui()
        self.assertEqual(codice, 0)
        self.assertIn("verifica", out)

    def test_versione(self):
        for opzione in ("versione", "--version", "-V"):
            self.assertIn(__version__, self.esegui(opzione)[1])

    def test_comando_sconosciuto(self):
        codice, _, err = self.esegui("boh")
        self.assertEqual(codice, 2)
        self.assertIn("boh", err)

    def test_comando_con_escape_ripulito(self):
        _, _, err = self.esegui("\x1b]0;titolo-finto\x07\x1b[2J")
        self.assertNotIn("\x1b", err)

    def test_argomenti_in_piu(self):
        self.assertEqual(self.esegui("versione", "extra")[0], 2)
        self.assertEqual(self.esegui("config", "cancella")[0], 2)

    def test_senza_colori_su_stringio(self):
        _, out, _ = self.esegui("verifica")
        self.assertNotIn("\x1b", out)

    def test_config_crea_e_mostra(self):
        codice, out, _ = self.esegui("config", "crea")
        self.assertEqual(codice, 0)
        self.assertEqual(self.esegui("config", "crea")[0], 1)  # non sovrascrive
        codice, out, _ = self.esegui("config")
        self.assertEqual(codice, 0)
        self.assertIn("interfaccia = auto", out)

    def test_interfacce_gira(self):
        self.assertIn(self.esegui("interfacce")[0], (0, 1))


if __name__ == "__main__":
    unittest.main()
