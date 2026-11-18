import io
import json
import os
import tempfile
import unittest
from unittest import mock

from strinix.comandi.integrati import crea_registro
from strinix.comandi.registro import FALLITO, OK, USO, Argomenti, Comando, Contesto, Registro
from strinix.ui.tema import Tema


def contesto(**kw):
    return Contesto(tema=Tema(0, True), out=io.StringIO(), err=io.StringIO(), **kw)


class TestTabella(unittest.TestCase):
    def test_nessun_duplicato_e_gruppi_validi(self):
        crea_registro()  # lancerebbe ValueError
        with self.assertRaises(ValueError):
            Registro([Comando(("a",), "generale", lambda c, a: 0), Comando(("a",), "generale", lambda c, a: 0)])
        with self.assertRaises(ValueError):
            Registro([Comando(("a",), "inventato", lambda c, a: 0)])

    def test_trova_la_parola_piu_lunga(self):
        r = crea_registro()
        c, resto = r.trova(["config", "crea"])
        self.assertEqual(c.nome, "config crea")
        c, resto = r.trova(["config", "--json"])
        self.assertEqual((c.nome, resto), ("config", ["--json"]))

    def test_completamento_ordinato(self):
        nomi = [c.nome for c in crea_registro().completa("")]
        self.assertEqual(nomi[0], "aiuto")
        self.assertNotIn("ascolta", nomi)  # nel cockpit si e' gia' in ascolto


class TestEsegui(unittest.TestCase):
    def setUp(self):
        self.r = crea_registro()
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self._tmp.name, "XDG_STATE_HOME": self._tmp.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def esegui(self, *args, **kw):
        ctx = contesto(**kw)
        return self.r.esegui(ctx, list(args)), ctx.out.getvalue(), ctx.err.getvalue()

    def test_sconosciuto_con_suggerimento(self):
        codice, _, err = self.esegui("verifca")
        self.assertEqual(codice, USO)
        self.assertIn("verifica", err)

    def test_sconosciuto_senza_suggerimento(self):
        codice, _, err = self.esegui("zzzzzz")
        self.assertEqual(codice, USO)
        self.assertNotIn("Intendevi", err)

    def test_flag_sconosciuto_non_ignorato(self):
        codice, _, err = self.esegui("verifica", "--jsno")
        self.assertEqual(codice, USO)
        self.assertIn("uso: strinix verifica [--json]", err)

    def test_trattino_doppio_e_opzioni_strane(self):
        for arg in ("--", "-", "--json=1", "-rf"):
            with self.subTest(arg=arg):
                self.assertEqual(self.esegui("versione", arg)[0], USO)

    def test_troppi_argomenti(self):
        self.assertEqual(self.esegui("versione", "extra")[0], USO)

    def test_sottocomando_sconosciuto(self):
        codice, _, err = self.esegui("config", "cancella")
        self.assertEqual(codice, USO)
        self.assertIn("config crea", err)

    def test_aiuto_di_un_comando(self):
        codice, out, _ = self.esegui("aiuto", "config")
        self.assertEqual(codice, OK)
        self.assertIn("uso: strinix config [--json]", out)
        self.assertIn("config crea", out)
        self.assertEqual(self.esegui("verifica", "--help")[0], OK)

    def test_aiuto_comando_inesistente(self):
        self.assertEqual(self.esegui("aiuto", "boh")[0], USO)

    def test_niente_shell_niente_testo_esterno(self):
        for args in (["$(id)"], ["`id`"], ["verifica;", "id"], ["\x1b[2J"], ["../../bin/sh"]):
            with self.subTest(args=args):
                codice, out, err = self.esegui(*args)
                self.assertEqual(codice, USO)
                self.assertNotIn("\x1b", err)
                self.assertNotIn("uid=", out + err)

    def test_comandi_solo_cockpit_da_terminale(self):
        for nome in ("dispositivi", "avvisi", "esci"):
            with self.subTest(nome=nome):
                codice, _, err = self.esegui(nome)
                self.assertEqual(codice, FALLITO)
                self.assertIn("cockpit", err)

    def test_ascolta_vietato_dentro_il_cockpit(self):
        self.assertEqual(self.esegui("ascolta", interattivo=True, nel_cockpit=True)[0], USO)
        self.assertEqual(self.esegui("tema", interattivo=True, nel_cockpit=True)[0], USO)
        self.assertEqual(self.esegui("versione", interattivo=True)[0], OK)   # nella console si

    def test_esci_nel_cockpit(self):
        ctx = contesto(interattivo=True)
        self.assertEqual(self.r.esegui(ctx, ["esci"]), OK)
        self.assertTrue(ctx.uscita)

    def test_json_stabile_e_senza_colori(self):
        for args in (["versione"], ["stato"], ["config"], ["tema"], ["interfacce"]):
            with self.subTest(args=args):
                codice, out, _ = self.esegui(*args, "--json")
                self.assertIn(codice, (OK, FALLITO))
                json.loads(out)
                self.assertNotIn("\x1b", out)
        _, out, _ = self.esegui("stato", "--json")
        self.assertTrue({"interfaccia", "ip", "router", "ascolto"} <= set(json.loads(out)))

    def test_verifica_json(self):
        codice, out, _ = self.esegui("verifica", "--json")
        dati = json.loads(out)
        self.assertEqual(dati["pronto"], codice == OK)
        self.assertTrue(all({"esito", "codice", "testo"} <= set(c) for c in dati["controlli"]))

    def test_json_escape_dati_ostili(self):
        ctx = contesto()
        ctx.stampa_json({"nome": "\x1b[2Jmalevolo"})
        self.assertNotIn("\x1b", ctx.out.getvalue())
        self.assertIn("\\u001b", ctx.out.getvalue())


class TestConSessione(unittest.TestCase):
    def setUp(self):
        from tests.aiuti import Finta
        self.r = crea_registro()
        self.finta = Finta()

    def esegui(self, *args):
        ctx = contesto(sessione=self.finta, interattivo=True)
        return self.r.esegui(ctx, list(args)), ctx.out.getvalue(), ctx.err.getvalue()

    def test_dispositivi(self):
        codice, out, _ = self.esegui("dispositivi")
        self.assertEqual(codice, OK)
        self.assertIn("16:37:f5:e1:63:98", out)
        self.assertIn("smartphone", out)
        dati = json.loads(self.esegui("dispositivi", "--json")[1])
        self.assertEqual({d["tipo"] for d in dati}, {"questo_computer", "router", "mac_casuale"})

    def test_avvisi(self):
        codice, out, _ = self.esegui("avvisi", "--json")
        dati = json.loads(out)
        self.assertEqual([a["tipo"] for a in dati], ["arp_cambio"])

    def test_stato_con_ascolto(self):
        dati = json.loads(self.esegui("stato", "--json")[1])
        self.assertTrue(dati["ascolto"]["attivo"])
        self.assertEqual(dati["ascolto"]["dispositivi"], 3)


class TestArgomenti(unittest.TestCase):
    def test_json(self):
        self.assertTrue(Argomenti(frozenset({"--json"})).json)
        self.assertFalse(Argomenti().json)


if __name__ == "__main__":
    unittest.main()
