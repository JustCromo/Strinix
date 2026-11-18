import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from strinix import ambiente


class TestControlla(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "net").mkdir()
        self.custode = self.dir / "cattura.py"
        self.custode.write_text("")

    def tearDown(self):
        self._tmp.cleanup()

    def esegui(self, **kw):
        return {c.chiave: c for c in ambiente.controlla(self.dir / "config.toml", sys_net=self.dir / "net",
                                                       route=self.dir / "route", custode=self.custode, **kw)}

    def test_senza_rete(self):
        esiti = self.esegui(euid=1000)
        self.assertEqual(esiti["verifica.interfaccia_manca"].esito, "errore")
        self.assertEqual(esiti["verifica.router_manca"].esito, "avviso")
        self.assertNotIn("verifica.root", esiti)

    def test_root_segnalato(self):
        self.assertEqual(self.esegui(euid=0)["verifica.root"].esito, "avviso")

    def test_config_rotta_e_un_errore(self):
        p = self.dir / "config.toml"
        p.write_text("interfaccia = 3\n")
        p.chmod(0o600)
        self.assertEqual(self.esegui(euid=1000)["config.interfaccia_non_valida"].esito, "errore")

    def test_sudo_mancante(self):
        with mock.patch("strinix.ambiente.shutil.which", return_value=None):
            self.assertEqual(self.esegui(euid=1000)["verifica.sudo_manca"].esito, "errore")

    def test_codice_di_cattura_modificabile_segnalato(self):
        with mock.patch("strinix.ambiente.os.access", return_value=True):
            self.assertEqual(self.esegui(euid=1000)["verifica.cattura_sviluppo"].esito, "avviso")

    def test_codice_di_cattura_protetto(self):
        with mock.patch("strinix.ambiente.os.access", return_value=False):
            self.assertNotIn("verifica.cattura_sviluppo", self.esegui(euid=1000))

    def test_modificabile_da_me_guarda_le_cartelle(self):
        visti = []
        with mock.patch("strinix.ambiente.os.access", side_effect=lambda p, m: visti.append(Path(p)) or False):
            ambiente.modificabile_da_me(Path("/usr/lib/x/cattura.py"))
        self.assertIn(Path("/usr/lib"), visti)
        self.assertIn(Path("/"), visti)
        self.assertEqual(os.W_OK, 2)


if __name__ == "__main__":
    unittest.main()
