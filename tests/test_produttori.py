import unittest
from unittest import mock

from strinix.decodifica import scapy_disponibile
from strinix.produttori import accorcia, produttore


class TestProduttori(unittest.TestCase):
    def test_accorcia(self):
        self.assertEqual(accorcia("Intel Corporate"), "Intel")
        self.assertEqual(accorcia("Cloud Network Technology Singapore Pte. Ltd."), "Cloud Network Technology")
        self.assertEqual(accorcia("Hewlett Packard Enterprise"), "Hewlett Packard")
        self.assertNotIn("\x1b", accorcia("\x1b[2JEvil Inc."))

    def test_mac_casuale_senza_produttore(self):
        self.assertIsNone(produttore("16:37:f5:e1:63:98"))

    @unittest.skipUnless(scapy_disponibile(), "Scapy non installato")
    def test_elenco_assente_o_rotto(self):
        with mock.patch("scapy.config.conf") as conf:
            conf.manufdb.lookup.side_effect = RuntimeError("rotto")
            self.assertIsNone(produttore("2c:8d:b1:71:af:ed"))
            conf.manufdb.lookup.side_effect = None
            conf.manufdb.lookup.return_value = ("2c:8d:b1:71:af:ed", "2c:8d:b1:71:af:ed")
            self.assertIsNone(produttore("2c:8d:b1:71:af:ed"))


if __name__ == "__main__":
    unittest.main()
