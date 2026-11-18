import threading
import time
import unittest

from strinix.decodifica import Pacchetto
from strinix.rete import Interfaccia, Strada
from strinix.sessione import Sessione
from strinix.sorgente import CatturaError, Frame

MIO = "2c:8d:b1:71:af:ed"
ROUTER = "60:d8:a4:25:bb:5b"


class CatturaFinta:
    def __init__(self, nome, frame=(), errore_avvio=None, errore_dopo=None):
        self.nome, self._frame = nome, list(frame)
        self.errore_avvio, self.errore_dopo = errore_avvio, errore_dopo
        self.chiusa = threading.Event()
        self.uscita = False

    def __enter__(self):
        return self

    def attendi(self):
        if self.errore_avvio:
            raise CatturaError(self.errore_avvio)

    def frame(self):
        yield from self._frame
        if self.errore_dopo:
            raise CatturaError(self.errore_dopo, codice=9)
        self.chiusa.wait(5)   # come il custode: resta aperto finche' Strinix non chiude

    def chiudi_ingresso(self):
        self.chiusa.set()

    def __exit__(self, *a):
        self.uscita = True


def decodifica_finta(dati):
    if dati == b"rotto":
        raise ValueError("illeggibile")
    mac, ip = dati.decode().split("|")
    return Pacchetto(mac, MIO, ip_sorgente=ip)


def sessione(**kw):
    i = Interfaccia("wlan0", "wifi", "attiva", MIO, "192.168.1.187")
    catture = []

    def crea(nome):
        c = CatturaFinta(nome, **kw)
        catture.append(c)
        return c
    s = Sessione(i, Strada("wlan0", "192.168.1.254"), ROUTER, cattura=crea, decodifica=decodifica_finta)
    return s, catture


def aspetta(condizione, secondi=3):
    fine = time.time() + secondi
    while time.time() < fine and not condizione():
        time.sleep(0.01)


class TestSessione(unittest.TestCase):
    def test_ascolta_e_si_ferma(self):
        ora = time.time()
        frame = [Frame(ora, 0, f"{ROUTER}|192.168.1.254".encode()), Frame(ora, 0, f"{MIO}|192.168.1.187".encode()),
                 Frame(ora, 0, b"rotto")]
        s, catture = sessione(frame=frame)
        s.avvia()
        aspetta(lambda: s.istantanea().pacchetti == 3)
        ist = s.istantanea()
        self.assertTrue(ist.attiva)
        self.assertEqual((ist.pacchetti, ist.illeggibili, len(ist.dispositivi)), (3, 1, 2))
        self.assertEqual(ist.etichette[ROUTER], "router")
        self.assertEqual(ist.etichette[MIO], "questo_computer")
        s.ferma()
        self.assertTrue(catture[0].uscita)
        self.assertFalse(s.istantanea().attiva)

    def test_istantanea_e_una_copia(self):
        s, _ = sessione(frame=[Frame(time.time(), 0, f"{ROUTER}|192.168.1.254".encode())])
        s.avvia()
        aspetta(lambda: s.istantanea().pacchetti == 1)
        ist = s.istantanea()
        ist.dispositivi[0].pacchetti = 999
        self.assertNotEqual(s.istantanea().dispositivi[0].pacchetti, 999)
        s.ferma()

    def test_errore_di_avvio(self):
        s, catture = sessione(errore_avvio="cattura.sudo")
        with self.assertRaises(CatturaError):
            s.avvia()
        self.assertTrue(catture[0].uscita)
        self.assertFalse(s.attiva)

    def test_errore_durante_ascolto(self):
        s, _ = sessione(errore_dopo="cattura.protocollo")
        s.avvia()
        aspetta(lambda: s.istantanea().errore is not None)
        self.assertEqual(s.istantanea().errore[0], "cattura.protocollo")
        self.assertFalse(s.istantanea().attiva)
        s.ferma()

    def test_eventi_limitati(self):
        ora = time.time()
        frame = [Frame(ora, 0, f"02:00:00:00:{i // 256:02x}:{i % 256:02x}|10.0.0.{i % 250 + 1}".encode())
                 for i in range(500)]
        s, _ = sessione(frame=frame)
        s._eventi = type(s._eventi)(maxlen=50)
        s.avvia()
        aspetta(lambda: s.istantanea().pacchetti == 500)
        self.assertEqual(len(s.istantanea().eventi), 50)
        s.ferma()

    def test_dns_cifrato_stima(self):
        s, _ = sessione()
        ist = s.istantanea()
        self.assertEqual(ist.dns_cifrato, "presto")


if __name__ == "__main__":
    unittest.main()
