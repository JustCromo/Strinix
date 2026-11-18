"""Test del custode (cattura.py) e del lato Strinix (sorgente.py)."""

import io
import os
import socket
import subprocess
import sys
import time
import unittest

from strinix import cattura
from strinix.sorgente import CUSTODE, Cattura, CatturaError, comando, leggi_frame

ROOT = os.geteuid() == 0


def flusso(*frame, magia=cattura.MAGIA):
    dati = magia
    for ora, tipo, corpo in frame:
        dati += cattura.INTESTAZIONE.pack(ora, tipo, len(corpo)) + corpo
    return io.BytesIO(dati)


def avvia_custode(*args, **kw):
    return subprocess.run([sys.executable, "-I", str(CUSTODE), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=10, **kw)


class TestProtocollo(unittest.TestCase):
    def test_frame_validi(self):
        f = list(leggi_frame(flusso((1.5, 0, b"abc"), (2.0, 4, b""))))
        self.assertEqual([(x.ora, x.tipo, x.dati) for x in f], [(1.5, 0, b"abc"), (2.0, 4, b"")])

    def test_flusso_vuoto(self):
        self.assertEqual(list(leggi_frame(io.BytesIO(b""))), [])

    def test_magia_sbagliata(self):
        with self.assertRaises(CatturaError):
            list(leggi_frame(flusso(magia=b"HTTP/1")))

    def test_lunghezza_esagerata(self):
        s = io.BytesIO(cattura.MAGIA + cattura.INTESTAZIONE.pack(1.0, 0, 10**9))
        with self.assertRaises(CatturaError):
            list(leggi_frame(s))

    def test_tipo_e_ora_non_validi(self):
        for ora, tipo in ((1.0, 200), (float("nan"), 0), (float("inf"), 0)):
            with self.subTest(ora=ora, tipo=tipo):
                s = io.BytesIO(cattura.MAGIA + cattura.INTESTAZIONE.pack(ora, tipo, 0))
                with self.assertRaises(CatturaError):
                    list(leggi_frame(s))

    def test_troncato(self):
        dati = flusso((1.0, 0, b"abcdef")).getvalue()
        for taglio in (len(cattura.MAGIA) + 3, len(dati) - 2):
            with self.subTest(taglio=taglio):
                with self.assertRaises(CatturaError):
                    list(leggi_frame(io.BytesIO(dati[:taglio])))


class TestComando(unittest.TestCase):
    def test_comando_isolato(self):
        cmd = comando("wlan0", python="/usr/bin/python3")
        self.assertEqual(cmd[:4], ["sudo", "--", "/usr/bin/python3", "-I"])
        self.assertTrue(os.path.isabs(cmd[4]))
        self.assertEqual(cmd[-1], "wlan0")

    def test_nome_malevolo_rifiutato_prima_di_sudo(self):
        for nome in ("-h", "--help", "wlan0;id", "../x", "\x1b[2J", "a" * 16):
            with self.subTest(nome=nome):
                with self.assertRaises(CatturaError):
                    Cattura(nome)

    def test_errore_del_custode_tradotto(self):
        cmd = [sys.executable, "-c", f"import sys; sys.exit({cattura.ESCI_INTERFACCIA})"]
        with self.assertRaises(CatturaError) as ctx:
            with Cattura("eth0", cmd=cmd) as c:
                list(c.frame())
        self.assertEqual(ctx.exception.chiave, "cattura.interfaccia")

    def test_flusso_finto_letto_e_chiuso(self):
        corpo = cattura.MAGIA + cattura.INTESTAZIONE.pack(1.0, 0, 3) + b"xyz"
        cmd = [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({corpo!r})"]
        with Cattura("eth0", cmd=cmd) as c:
            self.assertEqual([f.dati for f in c.frame()], [b"xyz"])


class TestCustode(unittest.TestCase):
    def test_argomenti_sbagliati(self):
        for args in ((), ("a", "b"), ("-h",), ("wlan0;id",), ("..",), ("a" * 16,)):
            with self.subTest(args=args):
                self.assertEqual(avvia_custode(*args).returncode, cattura.ESCI_USO)

    @unittest.skipIf(ROOT, "da root il custode partirebbe davvero")
    def test_senza_root_si_ferma(self):
        r = avvia_custode("lo")
        self.assertEqual(r.returncode, cattura.ESCI_PRIVILEGI)
        self.assertEqual(r.stdout, b"")

    @unittest.skipUnless(ROOT, "serve root")
    def test_interfaccia_inesistente(self):
        self.assertEqual(avvia_custode("nonesiste9").returncode, cattura.ESCI_INTERFACCIA)

    @unittest.skipUnless(ROOT and hasattr(socket, "AF_PACKET"), "serve root (gira nell'ambiente di prova)")
    def test_cattura_vera_su_loopback(self):
        proc = subprocess.Popen([sys.executable, "-I", str(CUSTODE), "lo"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(proc.stdout.read(len(cattura.MAGIA)), cattura.MAGIA)
            with open(f"/proc/{proc.pid}/status") as f:
                stato = dict(r.split(":", 1) for r in f.read().splitlines() if ":" in r)
            self.assertEqual(stato["Uid"].split(), ["65534"] * 4)   # diventato nobody
            self.assertEqual(int(stato["CapEff"].strip(), 16), 0)   # nessun permesso
            self.assertEqual(stato["NoNewPrivs"].strip(), "1")
            segno = b"strinix-prova-" + str(time.time()).encode()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                for _ in range(3):
                    s.sendto(segno, ("127.0.0.1", 9))
            visto = False
            deadline = time.time() + 5
            while not visto and time.time() < deadline:
                testa = proc.stdout.read(cattura.INTESTAZIONE.size)
                _, _, n = cattura.INTESTAZIONE.unpack(testa)
                visto = segno in proc.stdout.read(n)
            self.assertTrue(visto)
        finally:
            proc.stdin.close()   # Strinix chiude: il custode deve fermarsi da solo
            self.assertEqual(proc.wait(timeout=5), 0)
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
