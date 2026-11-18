import json
import re
import string
import unittest
from pathlib import Path

from strinix import messaggi
from strinix.messaggi import t
from strinix.terminale import pulisci

PREFISSI = ("aiuto|cli|config|verifica|interfacce|ascolta|evento|cattura|etichetta|descr|gruppo|stato|tipo"
            "|tema|console|cockpit|rete|pannello|vuoto|dettaglio|dispositivi|avvisi")
RADICE = Path(__file__).resolve().parent.parent / "strinix"


class TestTesti(unittest.TestCase):
    def test_ogni_chiave_usata_esiste(self):
        testi = messaggi.carica("it")
        usate = set()
        for f in RADICE.rglob("*.py"):
            sorgente = f.read_text(encoding="utf-8")
            usate |= set(re.findall(r'"((?:%s)\.[a-z_.]+)"' % PREFISSI, sorgente))
        usate.discard("config.toml")
        mancanti = sorted(k for k in usate if k not in testi and not k.endswith("."))
        self.assertEqual(mancanti, [])

    def test_tipi_e_stati_tradotti(self):
        testi = messaggi.carica("it")
        for tipo in ("wifi", "cablata", "loopback", "virtuale"):
            self.assertIn("interfacce.tipo." + tipo, testi)
        for stato in ("attiva", "spenta", "sconosciuto"):
            self.assertIn("interfacce.stato." + stato, testi)
        for etichetta in ("questo_computer", "router", "nessuna"):
            self.assertIn("etichetta." + etichetta, testi)
        from strinix.sorgente import ERRORI
        for chiave in ERRORI.values():
            self.assertIn(chiave, testi)

    def test_chiavi_costruite_al_volo(self):
        from strinix.comandi.integrati import LIVELLI, crea_registro
        from strinix.comandi.registro import GRUPPI
        from strinix.tui.cockpit import PANNELLI
        from strinix.ui.tema import RUOLI_TESTO
        testi = messaggi.carica("it")
        attese = ["descr." + "_".join(c.parole) for c in crea_registro().comandi.values()]
        attese += ["gruppo." + g for g in GRUPPI]
        attese += ["pannello." + p for p in PANNELLI] + ["vuoto." + p for p in PANNELLI]
        attese += ["tema.ruolo." + r for r in RUOLI_TESTO] + ["tema.colori." + v for v in LIVELLI.values()]
        attese += ["tipo." + e for e in ("questo_computer", "router", "mac_casuale", "nessuna")]
        attese += ["etichetta." + e for e in ("questo_computer", "router", "mac_casuale", "nessuna")]
        self.assertEqual([k for k in attese if k not in testi], [])

    def test_modelli_ben_formati(self):
        for chiave, modello in messaggi.carica("it").items():
            campi = [c[1] for c in string.Formatter().parse(modello) if c[1] is not None]
            for campo in campi:  # solo nomi semplici: niente attributi o indici nei modelli
                self.assertRegex(campo, r"^[a-z_]+$", chiave)

    def test_json_valido(self):
        dati = json.loads((RADICE / "lingue" / "it.json").read_text(encoding="utf-8"))
        self.assertTrue(all(isinstance(v, str) for v in dati.values()))

    def test_lingua_sconosciuta(self):
        with self.assertRaises(ValueError):
            messaggi.carica("../../etc/passwd")

    def test_chiave_inesistente_non_esplode(self):
        self.assertEqual(t("non.esiste"), "non.esiste")

    def test_valore_chiamato_chiave(self):
        self.assertIn("pippo", t("config.chiave_sconosciuta", chiave="pippo"))


class TestPulizia(unittest.TestCase):
    def test_toglie_escape_ansi(self):
        self.assertNotIn("\x1b", pulisci("\x1b[2J\x1b]0;titolo\x07ciao"))

    def test_toglie_a_capo_e_c1(self):
        s = pulisci("a\nb\rc\x9bd\x00e\x7f")
        self.assertEqual(s, "a?b?c?d?e?")

    def test_limita_lunghezza(self):
        self.assertLessEqual(len(pulisci("x" * 10_000)), 200)

    def test_t_ripulisce_i_valori(self):
        s = t("cli.sconosciuto", comando="\x1b[31mfinto\x1b[0m")
        self.assertNotIn("\x1b", s)

    def test_valori_con_graffe_non_interpretati(self):
        s = t("cli.sconosciuto", comando="{__class__}")
        self.assertIn("{__class__}", s)



if __name__ == "__main__":
    unittest.main()
