import os
import unittest

from strinix.decodifica import Pacchetto, decodifica, scapy_disponibile

if scapy_disponibile():
    from scapy.layers.dns import DNS, DNSQR, DNSRR
    from scapy.layers.inet import IP, TCP, UDP
    from scapy.layers.l2 import ARP, Ether

MIO = "2c:8d:b1:71:af:ed"
ROUTER = "aa:bb:cc:00:00:01"


@unittest.skipUnless(scapy_disponibile(), "Scapy non installato")
class TestDecodifica(unittest.TestCase):
    def test_arp(self):
        p = decodifica(bytes(Ether(src=ROUTER, dst="ff:ff:ff:ff:ff:ff") /
                             ARP(op=2, hwsrc=ROUTER, psrc="192.168.1.254", pdst="192.168.1.187")))
        self.assertEqual(p.arp.ip, "192.168.1.254")
        self.assertEqual(p.arp.mac, ROUTER)
        self.assertEqual(p.arp.operazione, 2)
        self.assertEqual(p.mac_destinazione, "ff:ff:ff:ff:ff:ff")

    def test_dns(self):
        p = decodifica(bytes(Ether(src=MIO, dst=ROUTER) / IP(src="192.168.1.187", dst="192.168.1.254") /
                             UDP(sport=40000, dport=53) / DNS(rd=1, qd=DNSQR(qname="github.com"))))
        self.assertEqual((p.ip_sorgente, p.dns), ("192.168.1.187", "github.com"))

    def test_risposta_dns_ignorata(self):
        p = decodifica(bytes(Ether() / IP() / UDP(sport=53, dport=53) /
                             DNS(qr=1, qd=DNSQR(qname="x.com"), an=DNSRR(rrname="x.com", rdata="1.2.3.4"))))
        self.assertIsNone(p.dns)

    def test_mdns_non_e_una_ricerca_internet(self):
        p = decodifica(bytes(Ether() / IP(dst="224.0.0.251") / UDP(sport=5353, dport=5353) /
                             DNS(qd=DNSQR(qname="_googlecast._tcp.local"))))
        self.assertIsNone(p.dns)

    def test_dns_su_tcp(self):
        p = decodifica(bytes(Ether() / IP(src="10.0.0.2") / TCP(sport=4000, dport=53) /
                             b"\x00\x1c" / DNS(qd=DNSQR(qname="a.it"))))
        self.assertIn(p.dns, ("a.it", None))  # Scapy decodifica il DNS su TCP solo in alcune versioni

    def test_nome_dns_con_escape(self):
        p = decodifica(bytes(Ether() / IP(src="10.0.0.2") / UDP(dport=53) /
                             DNS(qd=DNSQR(qname=b"\x1b[2Jmalevolo.com"))))
        self.assertTrue(p.dns is None or "malevolo" in p.dns)  # la pulizia per lo schermo la fa t()

    def test_spazzatura_non_esplode(self):
        for dati in (b"", b"\x00", os.urandom(14), os.urandom(60), os.urandom(1500), b"\xff" * 65535):
            with self.subTest(n=len(dati)):
                try:
                    p = decodifica(dati)
                except ValueError:
                    continue
                self.assertIsInstance(p, Pacchetto)

    def test_arp_con_campi_assurdi(self):
        p = decodifica(bytes(Ether() / ARP(op=99, hwsrc=ROUTER, psrc="192.168.1.1", pdst="192.168.1.2")))
        self.assertIsNone(p.arp)

    def test_frame_troncati(self):
        intero = bytes(Ether(src=MIO) / IP(src="192.168.1.187") / UDP(dport=53) / DNS(qd=DNSQR(qname="a.com")))
        for taglio in range(0, len(intero), 7):
            try:
                decodifica(intero[:taglio])
            except ValueError:
                pass


if __name__ == "__main__":
    unittest.main()
