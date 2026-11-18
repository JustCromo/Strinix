import tempfile
import unittest
from pathlib import Path

from strinix import rete
from strinix.rete import Interfaccia, Strada

INTESTAZIONE = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.net = self.dir / "net"
        self.net.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def interfaccia(self, nome, *, stato="up", mac="aa:bb:cc:dd:ee:ff", tipo="1", wifi=False, hardware=True):
        d = self.net / nome
        d.mkdir()
        (d / "operstate").write_text(stato + "\n")
        (d / "address").write_text(mac + "\n")
        (d / "type").write_text(tipo + "\n")
        if wifi:
            (d / "wireless").mkdir()
        if hardware:
            (d / "device").mkdir()

    def route(self, *righe):
        p = self.dir / "route"
        p.write_text(INTESTAZIONE + "".join(r + "\n" for r in righe))
        return p


class TestElenca(Base):
    def test_tipi_e_stati(self):
        self.interfaccia("lo", stato="unknown", mac="00:00:00:00:00:00", tipo="772", hardware=False)
        self.interfaccia("wlan0", wifi=True)
        self.interfaccia("enp3s0", stato="down")
        self.interfaccia("docker0", hardware=False)
        per_nome = {i.nome: i for i in rete.elenca(self.net, leggi_ipv4=lambda n: None)}
        self.assertEqual(per_nome["lo"].tipo, "loopback")
        self.assertIsNone(per_nome["lo"].mac)
        self.assertEqual(per_nome["wlan0"].tipo, "wifi")
        self.assertEqual(per_nome["wlan0"].stato, "attiva")
        self.assertEqual(per_nome["enp3s0"].tipo, "cablata")
        self.assertEqual(per_nome["enp3s0"].stato, "spenta")
        self.assertEqual(per_nome["docker0"].tipo, "virtuale")

    def test_nomi_strani_ignorati(self):
        self.interfaccia("eth0")
        self.interfaccia("x" * 20)
        self.interfaccia("a b")
        nomi = [i.nome for i in rete.elenca(self.net, leggi_ipv4=lambda n: None)]
        self.assertEqual(nomi, ["eth0"])

    def test_mac_malformato_scartato(self):
        self.interfaccia("eth0", mac="\x1b[2Jnon-un-mac")
        self.assertIsNone(rete.elenca(self.net, leggi_ipv4=lambda n: None)[0].mac)

    def test_file_enormi_letti_solo_in_parte(self):
        self.interfaccia("eth0")
        (self.net / "eth0" / "operstate").write_text("up" + "x" * 1_000_000)
        self.assertEqual(rete.elenca(self.net, leggi_ipv4=lambda n: None)[0].stato, "sconosciuto")

    def test_cartella_mancante(self):
        self.assertEqual(rete.elenca(self.dir / "non-esiste"), [])

    def test_ipv4_interfaccia_inesistente(self):
        self.assertIsNone(rete.ipv4_di("nonesiste0"))


class TestStrada(Base):
    def test_gateway_little_endian(self):
        p = self.route("wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0",
                       "wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0")
        self.assertEqual(rete.strada_predefinita(p), Strada("wlan0", "192.168.1.1"))

    def test_metrica_piu_bassa_vince(self):
        p = self.route("wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0",
                       "enp3s0\t00000000\t0100000A\t0003\t0\t0\t100\t00000000\t0\t0\t0")
        self.assertEqual(rete.strada_predefinita(p), Strada("enp3s0", "10.0.0.1"))

    def test_righe_malformate_ignorate(self):
        p = self.route("rotto", "eth0\t00000000\tZZZZ\t0003\t0\t0\t1\t0\t0\t0\t0",
                       "eth0\t00000000\t0101A8C0\tXX\t0\t0\t1\t0\t0\t0\t0",
                       "eth0\t00000000\tFFFFFFFFFF\t0003\t0\t0\t1\t0\t0\t0\t0",
                       "\x1b[2J\t00000000\t0101A8C0\t0003\t0\t0\t1\t0\t0\t0\t0",
                       "eth0\t00000000\t0101A8C0\t0001\t0\t0\t1\t0\t0\t0\t0")  # senza flag gateway
        self.assertIsNone(rete.strada_predefinita(p))

    def test_file_mancante(self):
        self.assertIsNone(rete.strada_predefinita(self.dir / "non-esiste"))


class TestScegli(unittest.TestCase):
    W = Interfaccia("wlan0", "wifi", "attiva", None, "192.168.1.5")
    E = Interfaccia("enp3s0", "cablata", "spenta", None, None)
    L = Interfaccia("lo", "loopback", "sconosciuto", None, "127.0.0.1")

    def test_auto_usa_la_strada(self):
        self.assertEqual(rete.scegli("auto", [self.E, self.W], Strada("wlan0", "192.168.1.1")), self.W)

    def test_auto_senza_strada_prende_una_attiva(self):
        self.assertEqual(rete.scegli("auto", [self.L, self.E, self.W], None), self.W)

    def test_auto_niente_di_utile(self):
        self.assertIsNone(rete.scegli("auto", [self.L, self.E], None))

    def test_scelta_esplicita(self):
        self.assertEqual(rete.scegli("enp3s0", [self.E, self.W], Strada("wlan0", "1.1.1.1")), self.E)

    def test_scelta_inesistente(self):
        self.assertIsNone(rete.scegli("eth9", [self.E, self.W], None))


if __name__ == "__main__":
    unittest.main()
