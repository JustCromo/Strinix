"""L'archivio di Strinix: un database SQLite con quello che l'agente ha visto.

L'agente scrive, il cockpit e i comandi leggono (in sola lettura). Dentro ci sono solo
*metadati*: chi c'e' in rete, chi si collega a cosa, gli avvisi. Mai il contenuto dei
pacchetti, mai password. I dati vecchi si cancellano da soli:

- eventi: 7 giorni
- traffico in chiaro: 30 giorni
- dispositivi non piu' visti: 90 giorni

Chi legge tratta il database come input non fidato: ogni campo viene controllato
(tipo, lunghezza, formato) prima di diventare un oggetto di Strinix.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sqlite3
import stat
import time
from dataclasses import replace
from pathlib import Path

from strinix import file_sicuri
from strinix.rete import Interfaccia, Strada
from strinix.sessione import Istantanea
from strinix.stato import Dispositivo, Evento, InChiaro

SCHEMA = 1
GIORNO = 86400.0
TIENI_EVENTI = 7 * GIORNO
TIENI_CHIARO = 30 * GIORNO
TIENI_DISPOSITIVI = 90 * GIORNO
EVENTI_LETTI = 2000             # quanti eventi recenti carica il cockpit
MAX_JSON = 16 * 1024
BATTITO_VIVO = 10.0             # se l'agente non da' segni di vita da 10 s, e' fermo
NOME_FILE = "archivio.db"

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_CHIAVE_EVENTO = re.compile(r"^evento\.[a-z_]{1,40}$")
LIVELLI = ("info", "avviso", "grave")

TABELLE = """
CREATE TABLE IF NOT EXISTS meta (chiave TEXT PRIMARY KEY, valore TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispositivi (
    mac TEXT PRIMARY KEY, primo REAL NOT NULL, ultimo REAL NOT NULL, pacchetti INTEGER NOT NULL,
    ip TEXT, produttore TEXT, nome TEXT, byte_inviati INTEGER NOT NULL, byte_ricevuti INTEGER NOT NULL,
    destinazioni TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eventi (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ora REAL NOT NULL, livello TEXT NOT NULL,
    chiave TEXT NOT NULL, valori TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS eventi_ora ON eventi (ora);
CREATE TABLE IF NOT EXISTS chiaro (
    chiave TEXT PRIMARY KEY, primo REAL NOT NULL, ultimo REAL NOT NULL, chi TEXT NOT NULL,
    protocollo TEXT NOT NULL, dove TEXT NOT NULL, credenziali INTEGER NOT NULL, volte INTEGER NOT NULL);
"""


class ArchivioError(Exception):
    """`motivo`: assente, link, non_file, permessi, proprietario, rotto, versione, occupato."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


def percorso_predefinito() -> Path:
    return file_sicuri.cartella_stato() / NOME_FILE


def _controlla_file(percorso: Path, *, mio: bool) -> None:
    """Il file deve essere un file normale, non un link, non modificabile da altri."""
    try:
        st = os.lstat(percorso)
    except FileNotFoundError:
        raise ArchivioError("assente") from None
    if stat.S_ISLNK(st.st_mode):
        raise ArchivioError("link")
    if not stat.S_ISREG(st.st_mode):
        raise ArchivioError("non_file")
    if st.st_mode & 0o002:
        raise ArchivioError("permessi")
    if mio and st.st_uid != os.geteuid():
        raise ArchivioError("proprietario")


def _testo(valore: object, massimo: int = 300) -> str | None:
    return valore[:massimo] if isinstance(valore, str) else None


def _ip(valore: object) -> str | None:
    if not isinstance(valore, str):
        return None
    try:
        return str(ipaddress.ip_address(valore))
    except ValueError:
        return None


def _numero(valore: object) -> float | None:
    return float(valore) if isinstance(valore, (int, float)) and not isinstance(valore, bool) else None


def _json(testo: object) -> object:
    if not isinstance(testo, str) or len(testo) > MAX_JSON:
        return None
    try:
        return json.loads(testo)
    except ValueError:
        return None


class Archivio:
    def __init__(self, percorso: Path | None = None, *, scrittura: bool = False) -> None:
        self.percorso = percorso or percorso_predefinito()
        self.scrittura = scrittura
        if scrittura:
            self._prepara()
            uri = f"file:{self.percorso}?mode=rw"
        else:
            _controlla_file(self.percorso, mio=False)
            uri = f"file:{self.percorso}?mode=ro"
        try:
            self.db = sqlite3.connect(uri, uri=True, timeout=2.0, isolation_level=None, check_same_thread=False)
            self.db.execute("PRAGMA trusted_schema = OFF")   # niente codice nascosto nello schema
            self.db.execute("PRAGMA busy_timeout = 2000")
            if scrittura:
                self.db.executescript(TABELLE)
                self._versione()
            else:
                self._controlla_versione()
        except sqlite3.DatabaseError:
            raise ArchivioError("rotto") from None

    def _prepara(self) -> None:
        cartella = self.percorso.parent
        cartella.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(self.percorso, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        except OSError:
            raise ArchivioError("link") from None
        _controlla_file(self.percorso, mio=True)

    def _versione(self) -> None:
        riga = self.db.execute("SELECT valore FROM meta WHERE chiave = 'schema'").fetchone()
        if riga is None:
            self.db.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
        elif riga[0] != str(SCHEMA):
            raise ArchivioError("versione")

    def _controlla_versione(self) -> None:
        riga = self.db.execute("SELECT valore FROM meta WHERE chiave = 'schema'").fetchone()
        if riga is None or riga[0] != str(SCHEMA):
            raise ArchivioError("versione")

    def chiudi(self) -> None:
        self.db.close()

    # ---------------------------------------------------------------- scrittura (agente)

    def _meta(self, chiave: str, valore: object) -> None:
        self.db.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(chiave) DO UPDATE SET valore = excluded.valore",
                        (chiave, json.dumps(valore)))

    def salva(self, ist: Istantanea, gia_salvati: int) -> int:
        """Salva lo stato e gli eventi nuovi. Restituisce quanti eventi risultano salvati in tutto."""
        nuovi = max(0, ist.eventi_totali - gia_salvati)
        da_scrivere = ist.eventi[-nuovi:] if nuovi else ()
        with self.db:
            self.db.execute("BEGIN")
            i, s = ist.interfaccia, ist.strada
            self._meta("rete", {
                "interfaccia": [i.nome, i.tipo, i.stato, i.mac, i.ipv4] if i else None,
                "strada": [s.interfaccia, s.gateway] if s else None, "router_mac": ist.router_mac})
            self._meta("stato", {
                "attiva": ist.attiva, "errore": list(ist.errore) if ist.errore else None, "inizio": ist.inizio,
                "pacchetti": ist.pacchetti, "illeggibili": ist.illeggibili, "pps": ist.pps,
                "storia_pps": list(ist.storia_pps), "etichette": ist.etichette,
                "dns_in_chiaro_mio": ist.dns_in_chiaro_mio})
            self._meta("battito", time.time())
            self.db.executemany(
                "INSERT INTO dispositivi VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mac) DO UPDATE SET "
                "ultimo=excluded.ultimo, pacchetti=excluded.pacchetti, ip=excluded.ip, produttore=excluded.produttore, "
                "nome=excluded.nome, byte_inviati=excluded.byte_inviati, byte_ricevuti=excluded.byte_ricevuti, "
                "destinazioni=excluded.destinazioni",
                [(d.mac, d.primo, d.ultimo, d.pacchetti, d.ip, d.produttore, d.nome, d.byte_inviati, d.byte_ricevuti,
                  json.dumps(d.destinazioni)) for d in ist.dispositivi])
            self.db.executemany("INSERT INTO eventi (ora, livello, chiave, valori) VALUES (?,?,?,?)",
                                [(e.ora, e.livello, e.chiave, json.dumps({k: str(v) for k, v in e.valori.items()}))
                                 for e in da_scrivere])
            self.db.executemany(
                "INSERT INTO chiaro VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chiave) DO UPDATE SET "
                "ultimo=excluded.ultimo, chi=excluded.chi, credenziali=excluded.credenziali, volte=excluded.volte",
                [(f"{c.chi}|{c.protocollo}|{c.dove}", c.primo, c.ultimo, c.chi, c.protocollo, c.dove,
                  int(c.credenziali), c.volte) for c in ist.chiaro])
        return gia_salvati + nuovi

    def ferma(self) -> None:
        """L'agente si ferma: lo scrive, cosi' chi legge non pensa che stia ancora ascoltando."""
        with self.db:
            self.db.execute("BEGIN")
            riga = self.db.execute("SELECT valore FROM meta WHERE chiave = 'stato'").fetchone()
            stato_ = _json(riga[0]) if riga else None
            if isinstance(stato_, dict):
                stato_["attiva"] = False
                self._meta("stato", stato_)

    def pulisci_vecchi(self, adesso: float | None = None) -> int:
        adesso = time.time() if adesso is None else adesso
        with self.db:
            self.db.execute("BEGIN")
            n = self.db.execute("DELETE FROM eventi WHERE ora < ?", (adesso - TIENI_EVENTI,)).rowcount
            n += self.db.execute("DELETE FROM chiaro WHERE ultimo < ?", (adesso - TIENI_CHIARO,)).rowcount
            n += self.db.execute("DELETE FROM dispositivi WHERE ultimo < ?", (adesso - TIENI_DISPOSITIVI,)).rowcount
        return n

    # ---------------------------------------------------------------- lettura (cockpit e comandi)

    def battito(self) -> float | None:
        riga = self.db.execute("SELECT valore FROM meta WHERE chiave = 'battito'").fetchone()
        return _numero(_json(riga[0])) if riga else None

    def agente_vivo(self, adesso: float | None = None) -> bool:
        b = self.battito()
        adesso = time.time() if adesso is None else adesso
        return b is not None and 0 <= adesso - b < BATTITO_VIVO

    def istantanea(self) -> Istantanea:
        meta = {k: _json(v) for k, v in self.db.execute("SELECT chiave, valore FROM meta WHERE chiave IN "
                                                        "('rete', 'stato')").fetchall()}
        rete = meta.get("rete") if isinstance(meta.get("rete"), dict) else {}
        st = meta.get("stato") if isinstance(meta.get("stato"), dict) else {}
        interfaccia = strada = None
        i = rete.get("interfaccia")
        if isinstance(i, list) and len(i) == 5 and all(x is None or isinstance(x, str) for x in i) and i[0]:
            interfaccia = Interfaccia(*(x[:64] if isinstance(x, str) else x for x in i))
        s = rete.get("strada")
        if isinstance(s, list) and len(s) == 2 and all(isinstance(x, str) for x in s):
            strada = Strada(s[0][:64], s[1][:64])
        router_mac = rete.get("router_mac") if isinstance(rete.get("router_mac"), str) else None
        errore = st.get("errore")
        errore = (errore[0], errore[1]) if (isinstance(errore, list) and len(errore) == 2 and isinstance(errore[0], str)
                                            and isinstance(errore[1], dict)) else None
        storia = st.get("storia_pps") if isinstance(st.get("storia_pps"), list) else []
        etichette = st.get("etichette") if isinstance(st.get("etichette"), dict) else {}
        return Istantanea(
            interfaccia=interfaccia, strada=strada, router_mac=router_mac,
            attiva=bool(st.get("attiva")) and self.agente_vivo(), errore=errore,
            inizio=_numero(st.get("inizio")) or time.time(),
            pacchetti=int(_numero(st.get("pacchetti")) or 0), illeggibili=int(_numero(st.get("illeggibili")) or 0),
            pps=_numero(st.get("pps")) or 0.0,
            eventi=tuple(self._eventi()), dispositivi=tuple(self._dispositivi()),
            etichette={k: v for k, v in etichette.items() if isinstance(k, str) and _MAC.match(k)
                       and v in ("questo_computer", "router", "mac_casuale", "nessuna")},
            dns_in_chiaro_mio=int(_numero(st.get("dns_in_chiaro_mio")) or 0),
            chiaro=tuple(self._chiaro()),
            storia_pps=tuple(int(x) for x in storia[-60:] if isinstance(x, int) and 0 <= x < 10**9),
            origine="agente",
        )

    def dispositivi_noti(self) -> list[Dispositivo]:
        """I dispositivi gia' visti, per ricordarli quando l'agente riparte."""
        return self._dispositivi()

    def conta(self) -> tuple[int, int]:
        """(dispositivi, eventi) nell'archivio."""
        d = self.db.execute("SELECT COUNT(*) FROM dispositivi").fetchone()[0]
        e = self.db.execute("SELECT COUNT(*) FROM eventi").fetchone()[0]
        return int(d), int(e)

    def _eventi(self) -> list[Evento]:
        righe = self.db.execute("SELECT ora, livello, chiave, valori FROM eventi ORDER BY id DESC LIMIT ?",
                                (EVENTI_LETTI,)).fetchall()
        eventi = []
        for ora, livello, chiave, valori in reversed(righe):
            dati = _json(valori)
            if (_numero(ora) is None or livello not in LIVELLI or not isinstance(chiave, str)
                    or not _CHIAVE_EVENTO.match(chiave) or not isinstance(dati, dict)):
                continue
            eventi.append(Evento(float(ora), livello, chiave,
                                 {str(k)[:40]: _testo(v) or "" for k, v in list(dati.items())[:20]}))
        return eventi

    def _dispositivi(self) -> list[Dispositivo]:
        schede = []
        for riga in self.db.execute("SELECT * FROM dispositivi ORDER BY primo LIMIT 4096").fetchall():
            mac, primo, ultimo, pacchetti, ip, produttore, nome, inviati, ricevuti, destinazioni = riga
            if not (isinstance(mac, str) and _MAC.match(mac) and _numero(primo) is not None and _numero(ultimo) is not None):
                continue
            dest = _json(destinazioni)
            dest = {str(k)[:253]: int(v) for k, v in dest.items() if isinstance(v, int)} if isinstance(dest, dict) else {}
            schede.append(Dispositivo(mac, float(primo), float(ultimo), int(_numero(pacchetti) or 0), _ip(ip),
                                      _testo(produttore, 40), _testo(nome, 63), int(_numero(inviati) or 0),
                                      int(_numero(ricevuti) or 0), dict(list(dest.items())[:16])))
        return schede

    def _chiaro(self) -> list[InChiaro]:
        visti = []
        for _, primo, ultimo, chi, protocollo, dove, credenziali, volte in self.db.execute(
                "SELECT * FROM chiaro ORDER BY ultimo DESC LIMIT 256").fetchall():
            if _numero(primo) is None or _numero(ultimo) is None or not all(
                    isinstance(x, str) for x in (chi, protocollo, dove)):
                continue
            visti.append(InChiaro(float(primo), float(ultimo), chi[:100], protocollo[:20], dove[:253],
                                  bool(credenziali), int(_numero(volte) or 1)))
        return visti


class FonteArchivio:
    """Una fonte per il cockpit e i comandi che legge l'archivio dell'agente (con una piccola cache)."""

    def __init__(self, archivio: Archivio, cache: float = 0.5) -> None:
        self.archivio, self.cache = archivio, cache
        self._ultima: tuple[float, Istantanea] | None = None

    def istantanea(self) -> Istantanea:
        adesso = time.monotonic()
        if self._ultima is None or adesso - self._ultima[0] > self.cache:
            try:
                self._ultima = (adesso, self.archivio.istantanea())
            except sqlite3.DatabaseError:
                if self._ultima is None:
                    raise
                self._ultima = (adesso, replace(self._ultima[1], attiva=False))
        return self._ultima[1]


def apri_fonte(percorso: Path | None = None) -> FonteArchivio | None:
    """L'archivio dell'agente, se c'e' ed e' leggibile; altrimenti None."""
    try:
        return FonteArchivio(Archivio(percorso))
    except ArchivioError:
        return None
