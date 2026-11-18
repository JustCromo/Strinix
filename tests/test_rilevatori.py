import unittest

from strinix.decodifica import Arp, Pacchetto
from strinix.rilevatori import SILENZIO, SOGLIA_PORTE, SOGLIA_SWEEP, SOGLIA_SWEEP_GRAVE, dominio_casuale, dominio_lungo, entropia
from strinix.stato import Memoria

MIO, ROUTER, ALTRO, TERZO = "2c:8d:b1:71:af:ed", "a0:ad:9f:68:1c:e8", "de:ad:be:ef:00:01", "00:11:22:33:44:55"
MIO_IP, ROUTER_IP = "192.168.50.110", "192.168.50.1"


def memoria():
    return Memoria(mio_mac=MIO, router_mac=ROUTER, mio_ip=MIO_IP, router_ip=ROUTER_IP, produttore=lambda m: None)


def chi_ha(mac, ip, cercato):
    return Pacchetto(mac, "ff:ff:ff:ff:ff:ff", arp=Arp(1, ip, mac, cercato))


def ce_lho(mac, ip):
    return Pacchetto(mac, "ff:ff:ff:ff:ff:ff", arp=Arp(2, ip, mac, ROUTER_IP))


def syn(src, porta, dst=MIO_IP, protocollo="tcp", mac=ALTRO):
    return Pacchetto(mac, MIO, ip_sorgente=src, ip_destinazione=dst, protocollo=protocollo,
                     porta_sorgente=50000, porta_destinazione=porta, nuova_connessione=True)


def tipi(eventi, prefisso="evento."):
    return [(e.chiave.removeprefix(prefisso), e.livello) for e in eventi]


class TestScansioneRete(unittest.TestCase):
    def sweep(self, m, mac, quanti, passo=0.05, inizio=0.0):
        eventi = []
        for i in range(quanti):
            eventi += m.osserva(chi_ha(mac, "192.168.50.66", f"192.168.50.{i + 2}"), inizio + i * passo)
        return [e for e in eventi if e.chiave == "evento.scansione_rete"]

    def test_scansione_vista_una_volta(self):
        e = self.sweep(memoria(), ALTRO, SOGLIA_SWEEP + 10)
        self.assertEqual(tipi(e), [("scansione_rete", "avviso")])
        self.assertEqual(e[0].valori["ip"], "192.168.50.66")

    def test_diventa_grave_e_lo_dice_ancora(self):
        e = self.sweep(memoria(), ALTRO, SOGLIA_SWEEP_GRAVE + 5, passo=0.01)
        self.assertEqual(tipi(e), [("scansione_rete", "avviso"), ("scansione_rete", "grave")])

    def test_sotto_soglia_o_lenta_niente(self):
        self.assertEqual(self.sweep(memoria(), ALTRO, SOGLIA_SWEEP - 1), [])
        self.assertEqual(self.sweep(memoria(), ALTRO, 60, passo=1.0), [])     # una richiesta al secondo: normale

    def test_router_e_questo_computer_solo_informazione(self):
        for mac in (ROUTER, MIO):
            with self.subTest(mac=mac):
                self.assertEqual(tipi(self.sweep(memoria(), mac, 40)), [("scansione_rete", "info")])

    def test_inondazione_di_mac_finti_non_riempie_la_memoria(self):
        m = memoria()
        for i in range(5000):
            m.osserva(chi_ha(f"02:00:00:00:{i // 256:02x}:{i % 256:02x}", "10.0.0.1", f"10.0.{i % 200}.1"), i * 0.001)
        self.assertLessEqual(len(m.rilevatori._arp_cercati._dati), 1024)

    def test_di_nuovo_dopo_il_silenzio(self):
        m = memoria()
        self.assertEqual(len(self.sweep(m, ALTRO, 30)), 1)
        self.assertEqual(len(self.sweep(m, ALTRO, 30, inizio=60)), 0)
        self.assertEqual(len(self.sweep(m, ALTRO, 30, inizio=SILENZIO + 100)), 1)


class TestScansionePorte(unittest.TestCase):
    def scansione(self, m, quanti, **kw):
        eventi = []
        for porta in range(1, quanti + 1):
            eventi += m.osserva(syn("192.168.50.116", porta, **kw), porta * 0.01)
        return [e for e in eventi if e.chiave == "evento.scansione_porte"]

    def test_scansione_contro_di_me(self):
        e = self.scansione(memoria(), SOGLIA_PORTE + 5)
        self.assertEqual(tipi(e), [("scansione_porte", "avviso")])
        self.assertIn("1, 2, 3", e[0].valori["porte"])

    def test_scansione_completa_grave(self):
        self.assertEqual(tipi(self.scansione(memoria(), 150))[-1], ("scansione_porte", "grave"))

    def test_non_contro_di_me_o_rumore_udp(self):
        self.assertEqual(self.scansione(memoria(), 50, dst="192.168.50.20"), [])
        m = memoria()
        eventi = []
        for porta in range(40000, 40100):             # risposte DNS che arrivano su porte alte a caso
            eventi += m.osserva(syn("192.168.50.1", porta, protocollo="udp"), 0)
        self.assertEqual([e for e in eventi if e.chiave == "evento.scansione_porte"], [])

    def test_io_che_mi_collego_non_conta(self):
        m = memoria()
        eventi = []
        for porta in range(1, 50):
            eventi += m.osserva(syn(MIO_IP, porta, dst="140.82.1.1", mac=MIO), 0)
        self.assertEqual([e for e in eventi if e.chiave == "evento.scansione_porte"], [])


class TestArpALivelli(unittest.TestCase):
    def test_qualcuno_si_spaccia_per_il_router(self):
        m = memoria()
        m.osserva(ce_lho(ROUTER, ROUTER_IP), 0)
        e = m.osserva(ce_lho(ALTRO, ROUTER_IP), 1)
        self.assertIn(("arp_router", "grave"), tipi(e))

    def test_router_gia_noto_dal_primo_istante(self):
        e = memoria().osserva(ce_lho(ALTRO, ROUTER_IP), 0)     # il vero router non ha ancora parlato
        self.assertIn(("arp_router", "grave"), tipi(e))

    def test_un_mac_tanti_ip(self):
        m = memoria()
        for i in range(3):
            m.osserva(ce_lho(TERZO, f"192.168.50.{20 + i}"), 0)
        eventi = []
        for i in range(3):
            eventi += m.osserva(ce_lho(ALTRO, f"192.168.50.{20 + i}"), 10 + i)
        self.assertIn(("arp_molti_ip", "grave"), tipi(eventi))

    def test_altalena(self):
        m = memoria()
        eventi = []
        for i in range(6):
            eventi += m.osserva(ce_lho(ALTRO if i % 2 else TERZO, "192.168.50.30"), i)
        self.assertIn(("arp_altalena", "grave"), tipi(eventi))

    def test_riassegnato_e_innocuo(self):
        m = memoria()
        m.osserva(ce_lho(TERZO, "192.168.50.30"), 0)
        e = m.osserva(ce_lho(ALTRO, "192.168.50.30"), 2000)
        self.assertIn(("arp_riassegnato", "info"), tipi(e))

    def test_cambio_semplice(self):
        m = memoria()
        m.osserva(ce_lho(TERZO, "192.168.50.30"), 0)
        e = m.osserva(ce_lho(ALTRO, "192.168.50.30"), 5)
        self.assertIn(("arp_cambio", "avviso"), tipi(e))


class TestDhcpFalso(unittest.TestCase):
    def offerta(self, m, mac, server, router, ora=0):
        p = Pacchetto(mac, "ff:ff:ff:ff:ff:ff", ip_sorgente=server, dhcp_tipo=2, dhcp_server=server, dhcp_router=router)
        return [e for e in m.osserva(p, ora) if e.chiave == "evento.dhcp_falso"]

    def test_il_router_e_normale(self):
        self.assertEqual(self.offerta(memoria(), ROUTER, ROUTER_IP, ROUTER_IP), [])

    def test_server_della_scuola_che_indica_il_router_vero(self):
        self.assertEqual(self.offerta(memoria(), TERZO, "10.0.0.5", ROUTER_IP), [])

    def test_server_che_propone_un_router_finto(self):
        e = self.offerta(memoria(), ALTRO, "192.168.50.66", "192.168.50.66")
        self.assertEqual(tipi(e), [("dhcp_falso", "grave")])
        self.assertEqual(e[0].valori["router"], "192.168.50.66")

    def test_senza_router_conosciuto_impara_il_primo(self):
        m = Memoria(produttore=lambda mac: None)
        self.assertEqual(self.offerta(m, TERZO, "10.0.0.1", None), [])
        self.assertEqual(self.offerta(m, TERZO, "10.0.0.1", None, 5), [])
        self.assertEqual(tipi(self.offerta(m, ALTRO, "10.0.0.66", None, 6)), [("dhcp_falso", "avviso")])

    def test_richieste_dei_client_ignorate(self):
        m = memoria()
        p = Pacchetto(ALTRO, "ff:ff:ff:ff:ff:ff", dhcp_tipo=3, dhcp_server="1.2.3.4")
        self.assertEqual([e for e in m.osserva(p, 0) if e.chiave == "evento.dhcp_falso"], [])


class TestDns(unittest.TestCase):
    def cerca(self, m, nome, ora=0):
        p = Pacchetto(MIO, ROUTER, ip_sorgente=MIO_IP, dns=nome)
        return [e for e in m.osserva(p, ora) if e.chiave in ("evento.dns_lungo", "evento.dns_casuale")]

    def test_nomi_normali_tranquilli(self):
        m = memoria()
        for nome in ("github.com", "www.google.com", "lh3.googleusercontent.com", "d3h8k2l9m4n7q1.cloudfront.net",
                     "waa-pa.clients6.google.com", "xn--80ak6aa92e.com", "printer.local", "1.50.168.192.in-addr.arpa",
                     "stackoverflow.com", "microsoftonline.com", "steamcommunity.com"):
            with self.subTest(nome=nome):
                self.assertEqual(self.cerca(m, nome), [])

    def test_dominio_casuale(self):
        self.assertEqual(tipi(self.cerca(memoria(), "xjw9q2kz8fpl.com")), [("dns_casuale", "avviso")])

    def test_tunneling(self):
        nome = "a" * 60 + ".dati.esfiltra.example.com"
        e = self.cerca(memoria(), nome)
        self.assertEqual(tipi(e), [("dns_lungo", "avviso")])
        self.assertEqual(e[0].valori["dominio"], "example.com")

    def test_una_volta_per_dominio(self):
        m = memoria()
        self.assertEqual(len(self.cerca(m, "x" * 70 + ".tunnel.example.com")), 1)
        self.assertEqual(len(self.cerca(m, "y" * 70 + ".tunnel.example.com", 1)), 0)


class TestFunzioni(unittest.TestCase):
    def test_entropia(self):
        self.assertEqual(entropia(""), 0)
        self.assertLess(entropia("google"), 2.5)
        self.assertGreater(entropia("xjw9q2kz8fpl"), 3.4)

    def test_lungo_e_casuale(self):
        self.assertTrue(dominio_lungo("a" * 101))
        self.assertTrue(dominio_lungo("b" * 51 + ".it"))
        self.assertFalse(dominio_casuale("x"))
        self.assertFalse(dominio_casuale("qwertzuiopasd.it"))   # lettere varie ma vocali normali e niente cifre


if __name__ == "__main__":
    unittest.main()
