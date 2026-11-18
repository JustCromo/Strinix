import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from strinix import agente
from strinix.archivio import BATTITO_VIVO, TIENI_EVENTI, Archivio, ArchivioError, FonteArchivio, apri_fonte
from strinix.decodifica import Pacchetto
from strinix.stato import Dispositivo, Evento, Memoria
from tests.aiuti import Finta


def istantanea(**kw):
    ist = Finta().istantanea()
    return replace(ist, eventi_totali=len(ist.eventi), **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.file = self.dir / "stato" / "archivio.db"

    def tearDown(self):
        self._tmp.cleanup()

    def scrivi(self, ist=None):
        a = Archivio(self.file, scrittura=True)
        a.salva(ist or istantanea(), 0)
        return a


class TestAndataERitorno(Base):
    def test_salva_e_rilegge(self):
        self.scrivi().chiudi()
        ist = Archivio(self.file).istantanea()
        self.assertEqual((len(ist.eventi), len(ist.dispositivi), len(ist.chiaro)), (5, 3, 2))
        self.assertTrue(ist.attiva)
        self.assertEqual(ist.origine, "agente")
        self.assertEqual(ist.interfaccia.nome, "wlp0s20f3")
        self.assertEqual(ist.dispositivi[0].destinazioni, {"github.com": 3, "www.google.com": 1})

    def test_eventi_solo_una_volta(self):
        a = self.scrivi()
        ist = istantanea()
        a.salva(ist, len(ist.eventi))
        nuovo = Evento(time.time(), "avviso", "evento.arp_cambio", {"ip": "1.2.3.4"})
        a.salva(replace(ist, eventi=ist.eventi + (nuovo,), eventi_totali=6), 5)
        self.assertEqual(a.conta()[1], 6)

    def test_permessi_privati(self):
        self.scrivi()
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.file.parent).st_mode & 0o777, 0o700)

    def test_sola_lettura_vera(self):
        self.scrivi().chiudi()
        lettore = Archivio(self.file)
        with self.assertRaises(sqlite3.OperationalError):
            lettore.db.execute("DELETE FROM eventi")

    def test_agente_fermo_o_muto(self):
        a = self.scrivi()
        self.assertTrue(Archivio(self.file).agente_vivo())
        self.assertFalse(Archivio(self.file).agente_vivo(time.time() + BATTITO_VIVO + 1))
        a.ferma()
        self.assertFalse(Archivio(self.file).istantanea().attiva)

    def test_scadenza(self):
        a = Archivio(self.file, scrittura=True)
        vecchio = Evento(time.time() - TIENI_EVENTI - 10, "info", "evento.dns", {"nome": "a.it"})
        ist = istantanea()
        a.salva(replace(ist, eventi=(vecchio,) + ist.eventi, eventi_totali=6), 0)
        self.assertGreaterEqual(a.pulisci_vecchi(), 1)
        self.assertEqual(a.conta()[1], 5)


class TestFileOstili(Base):
    def test_link_rifiutato(self):
        bersaglio = self.dir / "altro.db"
        bersaglio.write_bytes(b"")
        self.file.parent.mkdir(parents=True)
        self.file.symlink_to(bersaglio)
        for scrittura in (True, False):
            with self.subTest(scrittura=scrittura):
                with self.assertRaises(ArchivioError) as ctx:
                    Archivio(self.file, scrittura=scrittura)
                self.assertEqual(ctx.exception.motivo, "link")

    def test_scrivibile_da_tutti_rifiutato(self):
        self.scrivi().chiudi()
        os.chmod(self.file, 0o666)
        with self.assertRaises(ArchivioError) as ctx:
            Archivio(self.file)
        self.assertEqual(ctx.exception.motivo, "permessi")

    def test_di_un_altro_utente_non_lo_scrivo(self):
        self.scrivi().chiudi()
        with mock.patch("strinix.archivio.os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ArchivioError):
                Archivio(self.file, scrittura=True)

    def test_file_non_database(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b"non sono un database " * 100)
        os.chmod(self.file, 0o600)
        with self.assertRaises(ArchivioError) as ctx:
            Archivio(self.file)
        self.assertIn(ctx.exception.motivo, ("rotto", "versione"))

    def test_assente(self):
        self.assertIsNone(apri_fonte(self.file))

    def test_righe_manomesse_scartate(self):
        a = self.scrivi()
        a.db.executemany("INSERT INTO eventi (ora, livello, chiave, valori) VALUES (?,?,?,?)", [
            (time.time(), "panico", "evento.dns", "{}"),                       # livello inventato
            (time.time(), "info", "evento.x; DROP TABLE eventi", "{}"),        # chiave strana
            ("adesso", "info", "evento.dns", "{}"),                            # ora non numerica
            (time.time(), "info", "evento.dns", "non json"),
            (time.time(), "info", "evento.dns", json.dumps({"x": "y" * 20000})),   # troppo grande
            (time.time(), "info", "evento.dns", json.dumps({"chi": "\x1b[2Jcattivo", "nome": "a.it"})),
        ])
        a.db.execute("INSERT INTO dispositivi VALUES ('non-un-mac', 0, 0, 0, NULL, NULL, NULL, 0, 0, '{}')")
        a.db.execute("INSERT INTO dispositivi VALUES ('aa:bb:cc:dd:ee:ff', 0, 0, 0, 5, NULL, NULL, 0, 0, '[1,2]')")
        ist = Archivio(self.file).istantanea()
        self.assertEqual(len(ist.eventi), 6)       # 5 buoni + quello con l'escape (che viene ripulito a schermo)
        self.assertNotIn("non-un-mac", [d.mac for d in ist.dispositivi])
        strano = next(d for d in ist.dispositivi if d.mac == "aa:bb:cc:dd:ee:ff")
        self.assertEqual((strano.ip, strano.destinazioni), (None, {}))

    def test_cockpit_su_archivio_manomesso_non_esce_escape(self):
        from strinix.comandi.integrati import crea_registro
        from strinix.tui.cockpit import Cockpit
        from strinix.ui.tema import Tema
        a = self.scrivi()
        a.db.execute("INSERT INTO eventi (ora, livello, chiave, valori) VALUES (?,?,?,?)",
                     (time.time(), "grave", "evento.arp_router", json.dumps({"ip": "\x1b]0;x\x07\x1b[2J", "ora": "z"})))
        c = Cockpit(crea_registro(), Tema(0, True), FonteArchivio(Archivio(self.file)))
        for r in c.righe(160, 40):
            self.assertNotIn("\x1b", r)


class TestFonte(Base):
    def test_cache(self):
        self.scrivi()
        fonte = FonteArchivio(Archivio(self.file), cache=60)
        self.assertIs(fonte.istantanea(), fonte.istantanea())


class SessioneFinta:
    def __init__(self, cicli_attivi=1000):
        self.n, self.cicli, self.fermata = 0, cicli_attivi, False

    def istantanea(self):
        self.n += 1
        ist = istantanea()
        if self.n > self.cicli:
            return replace(ist, attiva=False, errore=("cattura.protocollo", {}))
        return ist

    def ferma(self):
        self.fermata = True


class TestAgente(Base):
    def test_salva_e_si_ferma_quando_chiesto(self):
        a = Archivio(self.file, scrittura=True)
        s = SessioneFinta()
        ferma = threading.Event()
        threading.Timer(0.2, ferma.set).start()
        self.assertIsNone(agente.esegui(s, a, ferma, intervallo=0.05))
        self.assertTrue(s.fermata)
        self.assertEqual(a.conta()[1], 5)                 # gli eventi una volta sola
        self.assertFalse(Archivio(self.file).istantanea().attiva)

    def test_si_ferma_se_la_cattura_muore(self):
        a = Archivio(self.file, scrittura=True)
        errore = agente.esegui(SessioneFinta(cicli_attivi=2), a, threading.Event(), intervallo=0.01)
        self.assertEqual(errore, "cattura.protocollo")

    def test_un_solo_agente(self):
        primo, secondo = agente.Lucchetto(self.dir / "a.lock"), agente.Lucchetto(self.dir / "a.lock")
        self.assertTrue(primo.prendi())
        self.assertFalse(secondo.prendi())
        primo.lascia()
        self.assertTrue(secondo.prendi())
        secondo.lascia()


class TestMemoriaLunga(unittest.TestCase):
    MAC = "de:ad:be:ef:00:01"

    def test_dispositivo_noto_non_e_nuovo_e_se_torna_lo_dice(self):
        m = Memoria(produttore=lambda mac: None)
        m.carica([Dispositivo(self.MAC, 0, 1000, 5, "192.168.1.9", None, "PC-Salotto")])
        e = m.osserva(Pacchetto(self.MAC, "ff:ff:ff:ff:ff:ff"), 1000 + 7200)
        chiavi = [x.chiave for x in e]
        self.assertNotIn("evento.nuovo_dispositivo", chiavi)
        self.assertIn("evento.ritorno", chiavi)
        self.assertEqual(m.osserva(Pacchetto(self.MAC, "ff:ff:ff:ff:ff:ff"), 1000 + 7300), [])

    def test_carica_rispetta_il_limite(self):
        m = Memoria(produttore=lambda mac: None, max_dispositivi=10)
        m.carica([Dispositivo(f"02:00:00:00:00:{i:02x}", i, i) for i in range(50)])
        self.assertEqual(len(m.dispositivi), 10)
        self.assertIn("02:00:00:00:00:31", m.dispositivi)          # tiene i piu' recenti


if __name__ == "__main__":
    unittest.main()
