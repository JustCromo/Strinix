import os
import tempfile
import unittest
from pathlib import Path

from strinix import cronologia, file_sicuri


class TestCronologia(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.file = Path(self._tmp.name) / "stato" / "cronologia"

    def tearDown(self):
        self._tmp.cleanup()

    def test_salva_e_carica(self):
        storia = cronologia.aggiungi([], "stato")
        storia = cronologia.aggiungi(storia, "stato")       # niente doppioni di fila
        storia = cronologia.aggiungi(storia, "aiuto")
        self.assertTrue(cronologia.salva(storia, self.file))
        self.assertEqual(cronologia.carica(self.file), ["stato", "aiuto"])
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.file.parent).st_mode & 0o777, 0o700)

    def test_limiti(self):
        storia = []
        for i in range(2000):
            storia = cronologia.aggiungi(storia, f"cmd{i}")
        self.assertEqual(len(storia), cronologia.MAX_RIGHE)
        self.assertLessEqual(len(cronologia.aggiungi([], "x" * 5000)[0]), cronologia.MAX_RIGA)

    def test_file_ostile_ignorato(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b"\x1b[2Jstato\n\xff\xfe\n")
        os.chmod(self.file, 0o600)
        righe = cronologia.carica(self.file)
        self.assertTrue(all("\x1b" not in r for r in righe))
        os.chmod(self.file, 0o666)
        self.assertEqual(cronologia.carica(self.file), [])

    def test_link_non_seguito_in_scrittura(self):
        self.file.parent.mkdir(parents=True)
        bersaglio = Path(self._tmp.name) / "bersaglio"
        bersaglio.write_text("mio")
        self.file.symlink_to(bersaglio)
        self.assertFalse(cronologia.salva(["stato"], self.file))
        self.assertEqual(bersaglio.read_text(), "mio")

    def test_scrivi_sovrascrive_solo_se_chiesto(self):
        p = Path(self._tmp.name) / "f"
        file_sicuri.scrivi(p, "a", sovrascrivi=False)
        with self.assertRaises(file_sicuri.FileNonSicuro):
            file_sicuri.scrivi(p, "b", sovrascrivi=False)
        file_sicuri.scrivi(p, "c", sovrascrivi=True)
        self.assertEqual(p.read_text(), "c")


if __name__ == "__main__":
    unittest.main()
