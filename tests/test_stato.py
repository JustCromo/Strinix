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
