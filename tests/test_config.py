import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from strinix import config as cfg


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.file = self.dir / "config.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def scrivi(self, testo, modo=0o600):
        self.file.write_bytes(testo if isinstance(testo, bytes) else testo.encode())
        os.chmod(self.file, modo)

    def errore(self):
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.carica(self.file)
        return ctx.exception.chiave


class TestCarica(Base):
    def test_nessun_file_valori_predefiniti(self):
        c = cfg.carica(self.file)
        self.assertEqual((c.interfaccia, c.lingua, c.percorso), ("auto", "it", None))

    def test_file_valido(self):
        self.scrivi('interfaccia = "wlan0"\nlingua = "it"\n')
        c = cfg.carica(self.file)
        self.assertEqual(c.interfaccia, "wlan0")
        self.assertEqual(c.percorso, self.file)

    def test_xdg_relativo_ignorato(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "relativo/strano"}):
            self.assertTrue(cfg.percorso_predefinito().is_absolute())

    def test_chiave_sconosciuta(self):
        self.scrivi('comando = "rm -rf /"\n')
        self.assertEqual(self.errore(), "config.chiave_sconosciuta")

    def test_interfacce_malevole(self):
        for nome in ("../../etc", "-h", "--help", "eth0; rm -rf ~", "\x1b[2Jeth0", "a" * 16, "", ".", "..", "eth 0"):
            with self.subTest(nome=nome):
                self.scrivi(f"interfaccia = {json.dumps(nome)}\n")
                self.assertEqual(self.errore(), "config.interfaccia_non_valida")

    def test_interfaccia_tipo_sbagliato(self):
        for valore in ("123", "[\"eth0\"]", "true", "{a = 1}"):
            with self.subTest(valore=valore):
                self.scrivi(f"interfaccia = {valore}\n")
                self.assertEqual(self.errore(), "config.interfaccia_non_valida")

    def test_lingua_non_supportata(self):
        self.scrivi('lingua = "../../etc/passwd"\n')
        self.assertEqual(self.errore(), "config.lingua_non_valida")

    def test_toml_rotto(self):
        self.scrivi('interfaccia = "wlan0\n')
        self.assertEqual(self.errore(), "config.sintassi")

    def test_non_utf8(self):
        self.scrivi(b'interfaccia = "\xff\xfe"\n')
        self.assertEqual(self.errore(), "config.sintassi")

    def test_troppo_grande(self):
        self.scrivi("# " + "x" * (cfg.MAX_BYTE + 10) + "\n")
        self.assertEqual(self.errore(), "config.troppo_grande")

    def test_scrivibile_da_altri(self):
        for modo in (0o620, 0o602, 0o666):
            with self.subTest(modo=oct(modo)):
                self.scrivi('lingua = "it"\n', modo)
                self.assertEqual(self.errore(), "config.permessi")

    def test_proprietario_diverso(self):
        self.scrivi('lingua = "it"\n')
        with mock.patch("strinix.config.os.geteuid", return_value=os.geteuid() + 1):
            self.assertEqual(self.errore(), "config.proprietario")

    def test_link_simbolico_non_seguito(self):
        vero = self.dir / "vero.toml"
        vero.write_text('lingua = "it"\n')
        os.chmod(vero, 0o600)
        self.file.symlink_to(vero)
        self.assertEqual(self.errore(), "config.link")

    def test_cartella_al_posto_del_file(self):
        self.file.mkdir()
        self.assertIn(self.errore(), ("config.non_file", "config.illeggibile"))

    def test_fifo_non_blocca(self):
        os.mkfifo(self.file, 0o600)
        esito = []
        th = threading.Thread(target=lambda: esito.append(self.errore()), daemon=True)
        th.start()
        th.join(3)
        self.assertFalse(th.is_alive(), "la lettura di una FIFO ha bloccato Strinix")
        self.assertEqual(esito, ["config.non_file"])


class TestCrea(Base):
    def test_crea_con_permessi_stretti(self):
        p = cfg.crea(self.dir / "nuova" / "config.toml")
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(p.parent).st_mode & 0o777, 0o700)
        self.assertEqual(cfg.carica(p).interfaccia, "auto")

    def test_non_sovrascrive(self):
        self.scrivi("# mio\n")
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.crea(self.file)
        self.assertEqual(ctx.exception.chiave, "config.esiste")
        self.assertEqual(self.file.read_text(), "# mio\n")

    def test_non_scrive_attraverso_un_link(self):
        bersaglio = self.dir / "bersaglio"
        self.file.symlink_to(bersaglio)
        with self.assertRaises(cfg.ConfigError):
            cfg.crea(self.file)
        self.assertFalse(bersaglio.exists())


if __name__ == "__main__":
    unittest.main()
