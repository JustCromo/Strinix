import io
import os
import tempfile
import unittest
from unittest import mock

from strinix.comandi.integrati import crea_registro
from strinix.tui import console
from strinix.ui.tema import Tema


class TestConsole(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self._tmp.name, "XDG_CONFIG_HOME": self._tmp.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def esegui(self, *righe):
        coda = list(righe)

        def leggi(_prompt):
            if not coda:
                raise EOFError
            r = coda.pop(0)
            if r is KeyboardInterrupt:
                raise KeyboardInterrupt
            return r
        out, err = io.StringIO(), io.StringIO()
        codice = console.esegui(crea_registro(), Tema(0, True), leggi=leggi, out=out, err=err)
        return codice, out.getvalue(), err.getvalue()

    def test_comandi_ed_esci(self):
        codice, out, err = self.esegui("versione", "", "verifca", "esci", "versione")
        self.assertEqual(codice, 0)
        self.assertEqual(out.count("strinix 0."), 1)   # dopo `esci` non esegue altro
        self.assertIn("verifica", err)

    def test_ctrl_c_e_ctrl_d(self):
        codice, out, _ = self.esegui(KeyboardInterrupt, "versione")
        self.assertEqual(codice, 0)
        self.assertIn("strinix 0.", out)

    def test_comandi_che_servono_il_cockpit(self):
        _, _, err = self.esegui("dispositivi")
        self.assertIn("cockpit", err)


if __name__ == "__main__":
    unittest.main()
