import tempfile
import unittest
from pathlib import Path

from strinix import ambiente


class TestCapacita(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def status(self, testo):
        p = self.dir / "status"
        p.write_text(testo)
        return p

    def test_legge_capeff(self):
        self.assertEqual(ambiente.capacita_effettive(self.status("Name:\tx\nCapEff:\t0000000000002000\n")), 0x2000)

    def test_status_rotto(self):
        for testo in ("", "CapEff:\n", "CapEff:\tzz\n"):
            with self.subTest(testo=testo):
                self.assertEqual(ambiente.capacita_effettive(self.status(testo)), 0)

    def test_net_raw(self):
        self.assertTrue(ambiente.puo_catturare(1 << 13))
        self.assertFalse(ambiente.puo_catturare(0))
        self.assertFalse(ambiente.puo_catturare(1 << 12 | 1 << 14))


class TestControlla(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "net").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def esegui(self, **kw):
        return {c.chiave: c for c in ambiente.controlla(self.dir / "config.toml", sys_net=self.dir / "net",
                                                       route=self.dir / "route", **kw)}

    def test_senza_rete_e_senza_permessi(self):
        esiti = self.esegui(cap=0, euid=1000)
        self.assertEqual(esiti["verifica.interfaccia_manca"].esito, "errore")
        self.assertEqual(esiti["verifica.router_manca"].esito, "avviso")
        self.assertEqual(esiti["verifica.cattura_manca"].esito, "avviso")
        self.assertNotIn("verifica.root", esiti)

    def test_root_segnalato(self):
        self.assertEqual(self.esegui(cap=1 << 13, euid=0)["verifica.root"].esito, "avviso")

    def test_config_rotta_e_un_errore(self):
        p = self.dir / "config.toml"
        p.write_text("interfaccia = 3\n")
        p.chmod(0o600)
        self.assertEqual(self.esegui(cap=0, euid=1000)["config.interfaccia_non_valida"].esito, "errore")


if __name__ == "__main__":
    unittest.main()
