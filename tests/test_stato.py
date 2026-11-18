import unittest

from strinix.decodifica import Arp, Pacchetto
from strinix.stato import DNS_SILENZIO, Memoria

MIO, ROUTER, ALTRO = "2c:8d:b1:71:af:ed", "aa:bb:cc:00:00:01", "de:ad:be:ef:00:01"


def arp(mac, ip, op=2, cercato="192.168.1.187"):
    return Pacchetto(mac, "ff:ff:ff:ff:ff:ff", arp=Arp(op, ip, mac, cercato))


def chiavi(eventi):
    return [e.chiave for e in eventi]


class TestDispositivi(unittest.TestCase):
    def test_nuovo_una_volta_sola(self):
        m = Memoria()
        self.assertEqual(chiavi(m.osserva(Pacchetto(ALTRO, MIO), 1)), ["evento.nuovo_dispositivo"])
        self.assertEqual(m.osserva(Pacchetto(ALTRO, MIO), 2), [])

    def test_etichette(self):
        m = Memoria(mio_mac=MIO, router_mac=ROUTER)
        self.assertEqual(m.osserva(Pacchetto(MIO, ROUTER), 1)[0].valori["etichetta"], "questo_computer")
        self.assertEqual(m.osserva(Pacchetto(ROUTER, MIO), 1)[0].valori["etichetta"], "router")

    def test_ip_da_arp(self):
        e = Memoria().osserva(arp(ROUTER, "192.168.1.254"), 1)
        self.assertEqual(e[0].valori["ip"], "192.168.1.254")

    def test_ip_pubblico_non_attribuito(self):
        # il router inoltra traffico da internet: l'IP sorgente non e' il suo
        e = Memoria().osserva(Pacchetto(ROUTER, MIO, ip_sorgente="140.82.121.4"), 1)
        self.assertEqual(e[0].valori["ip"], "-")

    def test_multicast_e_broadcast_non_sono_dispositivi(self):
        m = Memoria()
        for mac in ("ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:fb", "33:33:00:00:00:01", "00:00:00:00:00:00"):
            self.assertEqual(m.osserva(Pacchetto(mac, MIO), 1), [])

    def test_inondazione_di_mac(self):
        m = Memoria(max_dispositivi=10)
        eventi = []
        for i in range(1000):
            eventi += m.osserva(Pacchetto(f"02:00:00:00:{i // 256:02x}:{i % 256:02x}", MIO), i)
        self.assertEqual(len(m.dispositivi), 10)
        self.assertEqual(chiavi(eventi).count("evento.memoria_piena"), 1)
        self.assertEqual(chiavi(eventi).count("evento.nuovo_dispositivo"), 10)


class TestArp(unittest.TestCase):
    def test_cambio_segnalato(self):
        m = Memoria()
        m.osserva(arp(ROUTER, "192.168.1.254"), 1)
        e = m.osserva(arp(ALTRO, "192.168.1.254"), 2)
        cambio = [x for x in e if x.chiave == "evento.arp_cambio"]
        self.assertEqual(len(cambio), 1)
        self.assertEqual(cambio[0].livello, "avviso")
        self.assertEqual((cambio[0].valori["prima"], cambio[0].valori["ora"]), (ROUTER, ALTRO))

    def test_stessa_associazione_silenziosa(self):
        m = Memoria()
        m.osserva(arp(ROUTER, "192.168.1.254"), 1)
        self.assertEqual(m.osserva(arp(ROUTER, "192.168.1.254", op=1), 2), [])

    def test_probe_arp_ignorato(self):
        m = Memoria()
        m.osserva(arp(ALTRO, "0.0.0.0"), 1)
        self.assertEqual(m.arp, {})

    def test_inondazione_di_ip(self):
        m = Memoria(max_dispositivi=5)
        for i in range(1, 200):
            m.osserva(arp(ROUTER, f"192.168.1.{i}"), i)
        self.assertEqual(len(m.arp), 5)


class TestDns(unittest.TestCase):
    def dns(self, nome, ip="192.168.1.187"):
        return Pacchetto(MIO, ROUTER, ip_sorgente=ip, dns=nome)

    def test_ripetizioni_silenziate(self):
        m = Memoria(mio_mac=MIO)
        self.assertIn("evento.dns", chiavi(m.osserva(self.dns("github.com"), 0)))
        self.assertNotIn("evento.dns", chiavi(m.osserva(self.dns("GitHub.com"), 10)))
        self.assertIn("evento.dns", chiavi(m.osserva(self.dns("github.com"), 10 + DNS_SILENZIO)))

    def test_memoria_dns_limitata(self):
        m = Memoria(max_dns=50)
        for i in range(5000):
            m.osserva(self.dns(f"{i}.esempio.com"), i)
        self.assertLessEqual(len(m.dns), 50)


if __name__ == "__main__":
    unittest.main()


from strinix.decodifica import Chiaro  # noqa: E402
from strinix.stato import CONNESSIONE_SILENZIO, MAX_CHIARO, MAX_CONNESSIONI, MAX_DESTINAZIONI  # noqa: E402


def senza_produttore(mac):
    return None


def tcp(src_mac=MIO, src="192.168.1.187", dst="140.82.121.4", porta=443, **kw):
    campi = dict(ip_sorgente=src, ip_destinazione=dst, protocollo="tcp", porta_sorgente=40000,
                 porta_destinazione=porta, nuova_connessione=True, lunghezza=100)
    campi.update(kw)
    return Pacchetto(src_mac, ROUTER, **campi)


class TestConnessioni(unittest.TestCase):
    def setUp(self):
        self.m = Memoria(mio_mac=MIO, router_mac=ROUTER, mio_ip="192.168.1.187", produttore=senza_produttore)

    def test_nome_dal_sni_e_dedup(self):
        e = [x for x in self.m.osserva(tcp(sni="github.com"), 0) if x.chiave == "evento.connessione"]
        self.assertEqual(e[0].valori["dove"], "github.com")
        self.assertEqual(e[0].valori["chi"], "questo computer")
        self.assertEqual(e[0].valori["servizio"], "HTTPS")
        self.assertNotIn("evento.connessione", chiavi(self.m.osserva(tcp(), 10)))
        self.assertIn("evento.connessione", chiavi(self.m.osserva(tcp(), 10 + CONNESSIONE_SILENZIO)))

    def test_nome_dalle_risposte_dns(self):
        self.m.osserva(Pacchetto(ROUTER, MIO, risposte_dns=(("youtube.com", "142.250.1.1"),)), 0)
        e = [x for x in self.m.osserva(tcp(dst="142.250.1.1"), 1) if x.chiave == "evento.connessione"]
        self.assertEqual(e[0].valori["dove"], "youtube.com")

    def test_rumore_ignorato(self):
        for p in (tcp(dst="224.0.0.251"), tcp(dst="255.255.255.255"), tcp(protocollo="udp", porta=50000),
                  tcp(nuova_connessione=False), tcp(src="8.8.8.8")):
            self.assertNotIn("evento.connessione", chiavi(self.m.osserva(p, 0)))

    def test_porta_sconosciuta_detta_chiara(self):
        e = [x for x in self.m.osserva(tcp(porta=4444), 0) if x.chiave == "evento.connessione"]
        self.assertEqual(e[0].valori["servizio"], "TCP 4444")

    def test_limiti(self):
        for i in range(MAX_CONNESSIONI + 500):
            self.m.osserva(tcp(dst=f"10.{i // 65536}.{i // 256 % 256}.{i % 256}", porta=443), i)
        self.assertLessEqual(len(self.m.connessioni), MAX_CONNESSIONI)
        self.assertLessEqual(len(self.m.dispositivi[MIO].destinazioni), MAX_DESTINAZIONI)


class TestSchede(unittest.TestCase):
    def test_traffico_nome_produttore(self):
        m = Memoria(mio_mac=MIO, produttore=lambda mac: "Intel" if mac == MIO else None)
        m.osserva(Pacchetto(MIO, ROUTER, lunghezza=500), 0)
        m.osserva(Pacchetto(ROUTER, MIO, lunghezza=1500), 0)
        self.assertEqual((m.dispositivi[MIO].byte_inviati, m.dispositivi[MIO].byte_ricevuti), (500, 1500))
        self.assertEqual(m.dispositivi[MIO].produttore, "Intel")
        e = m.osserva(Pacchetto(ALTRO, MIO, nome_host="iPhone-di-Ivan"), 1)
        self.assertIn("evento.nome", chiavi(e))
        self.assertEqual(m.osserva(Pacchetto(ALTRO, MIO, nome_host="iPhone-di-Ivan"), 2), [])
        self.assertEqual(m.chi(ALTRO, "192.168.1.9"), "iPhone-di-Ivan")

    def test_online(self):
        m = Memoria(produttore=senza_produttore)
        m.osserva(Pacchetto(ALTRO, MIO), 0)
        self.assertTrue(m.dispositivi[ALTRO].online(100))
        self.assertFalse(m.dispositivi[ALTRO].online(1000))


class TestChiaro(unittest.TestCase):
    def setUp(self):
        self.m = Memoria(mio_mac=MIO, produttore=senza_produttore)

    def chiaro(self, password, ora, dove="example.com"):
        return self.m.osserva(tcp(porta=80, chiaro=Chiaro("HTTP", dove, password)), ora)

    def test_avviso_poi_password_una_volta(self):
        e = self.chiaro(False, 0)
        self.assertEqual([x.livello for x in e if x.chiave.startswith("evento.chiaro")], ["avviso"])
        e = self.chiaro(True, 1)
        self.assertEqual([x.livello for x in e if x.chiave.startswith("evento.chiaro")], ["grave"])
        self.assertEqual([x for x in self.chiaro(True, 2) if x.chiave.startswith("evento.chiaro")], [])
        visto = list(self.m.chiaro.values())[0]
        self.assertEqual((visto.volte, visto.credenziali, visto.dove), (3, True, "example.com"))

    def test_limite(self):
        for i in range(MAX_CHIARO + 100):
            self.chiaro(False, i, dove=f"s{i}.it")
        self.assertLessEqual(len(self.m.chiaro), MAX_CHIARO)
