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


def client_hello(nome: bytes, *, tronca_ext: int = 0) -> bytes:
    """Un ClientHello TLS minimo con l'estensione server_name."""
    sni = b"\x00" + len(nome).to_bytes(2, "big") + nome
    sni = len(sni).to_bytes(2, "big") + sni
    ext = b"\x00\x00" + len(sni).to_bytes(2, "big") + sni
    corpo = b"\x03\x03" + b"\x00" * 32 + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00"
    corpo += (len(ext) + tronca_ext).to_bytes(2, "big") + ext
    hs = b"\x01" + len(corpo).to_bytes(3, "big") + corpo
    return b"\x16\x03\x01" + len(hs).to_bytes(2, "big") + hs


class TestSni(unittest.TestCase):
    def test_nome_letto(self):
        from strinix.decodifica import sni_da_client_hello
        self.assertEqual(sni_da_client_hello(client_hello(b"github.com")), "github.com")

    def test_nomi_ostili_rifiutati(self):
        from strinix.decodifica import sni_da_client_hello
        for nome in (b"\x1b[2Jevil.com", b"a" * 300, b"evil com", b"", b"\xff\xfe.com", b"-x.com"):
            with self.subTest(nome=nome):
                self.assertIsNone(sni_da_client_hello(client_hello(nome)))

    def test_lunghezze_bugiarde_e_troncamenti(self):
        from strinix.decodifica import sni_da_client_hello
        buono = client_hello(b"github.com")
        for taglio in range(len(buono)):
            self.assertIn(sni_da_client_hello(buono[:taglio]), (None, "github.com"))
        self.assertIn(sni_da_client_hello(client_hello(b"github.com", tronca_ext=500)), (None, "github.com"))
        for _ in range(300):
            sni_da_client_hello(b"\x16\x03\x01" + os.urandom(80))


@unittest.skipUnless(scapy_disponibile(), "Scapy non installato")
class TestInChiaro(unittest.TestCase):
    def tcp(self, porta, carico, flags="PA"):
        return decodifica(bytes(Ether(src=MIO, dst=ROUTER) / IP(src="192.168.1.187", dst="93.184.216.34") /
                                TCP(sport=40000, dport=porta, flags=flags) / carico))

    def test_http_sito_senza_password(self):
        p = self.tcp(80, b"GET /pagina HTTP/1.1\r\nHost: example.com:80\r\nUser-Agent: x\r\n\r\n")
        self.assertEqual((p.chiaro.protocollo, p.chiaro.dove, p.chiaro.credenziali), ("HTTP", "example.com", False))

    def test_http_password(self):
        for carico in (b"POST /login HTTP/1.1\r\nHost: a.it\r\n\r\nuser=ivan&password=segreta",
                       b"GET / HTTP/1.1\r\nHost: a.it\r\nAuthorization: Basic aXZhbjpzZWdyZXRh\r\n\r\n"):
            with self.subTest(carico=carico[:20]):
                p = self.tcp(80, carico)
                self.assertTrue(p.chiaro.credenziali)
                self.assertNotIn("segreta", repr(p))      # la password non entra mai in Strinix

    def test_altri_protocolli(self):
        casi = {(21, b"PASS segreta\r\n"): ("FTP", True), (21, b"USER ivan\r\n"): ("FTP", False),
                (110, b"PASS segreta\r\n"): ("POP3", True), (143, b"a1 LOGIN ivan segreta\r\n"): ("IMAP", True),
                (25, b"AUTH PLAIN AGl2YW4Ac2VncmV0YQ==\r\n"): ("SMTP", True), (23, b"\xff\xfb\x01"): ("Telnet", False)}
        for (porta, carico), (nome, password) in casi.items():
            with self.subTest(porta=porta, carico=carico[:10]):
                p = self.tcp(porta, carico)
                self.assertEqual((p.chiaro.protocollo, p.chiaro.credenziali), (nome, password))
                self.assertNotIn("segreta", repr(p))

    def test_traffico_cifrato_o_strano_ignorato(self):
        self.assertIsNone(self.tcp(443, client_hello(b"a.com")).chiaro)
        self.assertIsNone(self.tcp(80, os.urandom(200)).chiaro)
        self.assertIsNone(self.tcp(21, b"\x16\x03\x01 tls").chiaro)

    def test_sni_e_connessione_nuova(self):
        syn = self.tcp(443, b"", flags="S")
        self.assertTrue(syn.nuova_connessione)
        self.assertFalse(self.tcp(443, b"", flags="SA").nuova_connessione)
        p = self.tcp(443, client_hello(b"github.com"))
        self.assertEqual((p.sni, p.porta_destinazione, p.protocollo), ("github.com", 443, "tcp"))


@unittest.skipUnless(scapy_disponibile(), "Scapy non installato")
class TestNomi(unittest.TestCase):
    def test_risposte_dns(self):
        p = decodifica(bytes(Ether() / IP(src="192.168.1.254", dst="192.168.1.187") / UDP(sport=53, dport=40000) /
                             DNS(qr=1, qd=DNSQR(qname="github.com"),
                                 an=DNSRR(rrname="github.com", type="A", rdata="140.82.121.4"))))
        self.assertEqual(p.risposte_dns, (("github.com", "140.82.121.4"),))

    def test_nome_da_mdns(self):
        p = decodifica(bytes(Ether(src=ROUTER) / IP(src="192.168.1.201", dst="224.0.0.251") / UDP(sport=5353, dport=5353) /
                             DNS(qr=1, an=DNSRR(rrname="iPhone-di-Ivan.local", type="A", rdata="192.168.1.201"))))
        self.assertEqual(p.nome_host, "iPhone-di-Ivan")

    def test_nome_da_dhcp(self):
        from scapy.layers.dhcp import BOOTP, DHCP
        for nome, atteso in ((b"PC-Salotto", "PC-Salotto"), (b"\x1b[2Jcattivo", None), (b"x" * 100, None)):
            with self.subTest(nome=nome):
                p = decodifica(bytes(Ether(src=ROUTER, dst="ff:ff:ff:ff:ff:ff") / IP(src="0.0.0.0", dst="255.255.255.255") /
                                     UDP(sport=68, dport=67) / BOOTP() /
                                     DHCP(options=[("message-type", "request"), ("hostname", nome), "end"])))
                self.assertEqual(p.nome_host, atteso)
