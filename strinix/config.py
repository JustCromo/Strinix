"""Configurazione di Strinix: ~/.config/strinix/config.toml (facoltativa).

Il file e' trattato come input non fidato: deve essere un file normale (niente link,
FIFO o dispositivi), tuo, non scrivibile da altri, piccolo, e con solo chiavi note.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from strinix.messaggi import LINGUE

# nomi interfaccia Linux: al massimo 15 caratteri (IFNAMSIZ - 1), solo caratteri innocui
_NOME_INTERFACCIA = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
MAX_BYTE = 16 * 1024
CHIAVI = ("interfaccia", "lingua")

MODELLO = """# Configurazione di Strinix
# interfaccia: "auto" usa quella con la strada verso internet (es. "wlan0", "enp3s0")
interfaccia = "auto"
# lingua dei messaggi (per ora solo "it")
lingua = "it"
"""


class ConfigError(Exception):
    """Errore di configurazione: `chiave` e' il messaggio da mostrare, `valori` i dettagli."""

    def __init__(self, chiave: str, /, **valori: object) -> None:
        super().__init__(chiave)
        self.chiave = chiave
        self.valori = valori


@dataclass(frozen=True)
class Config:
    interfaccia: str = "auto"
    lingua: str = "it"
    percorso: Path | None = None  # None = nessun file, valori predefiniti


def nome_interfaccia_valido(nome: object) -> bool:
    return isinstance(nome, str) and bool(_NOME_INTERFACCIA.match(nome)) and nome not in (".", "..")


def percorso_predefinito() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    radice = Path(base) if base and os.path.isabs(base) else Path.home() / ".config"
    return radice / "strinix" / "config.toml"


def _leggi_sicuro(percorso: Path) -> bytes | None:
    try:
        # O_NONBLOCK: una FIFO al posto del file non deve bloccare Strinix
        fd = os.open(percorso, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConfigError("config.link", percorso=percorso) from None
        raise ConfigError("config.illeggibile", percorso=percorso) from None
    try:
        st = os.fstat(fd)  # controlli sul file gia' aperto: nessuno puo' scambiarlo nel frattempo
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError("config.non_file", percorso=percorso)
        if st.st_uid != os.geteuid():
            raise ConfigError("config.proprietario", percorso=percorso)
        if st.st_mode & 0o022:
            raise ConfigError("config.permessi", percorso=percorso)
        dati = os.read(fd, MAX_BYTE + 1)
    finally:
        os.close(fd)
    if len(dati) > MAX_BYTE:
        raise ConfigError("config.troppo_grande", percorso=percorso, massimo=MAX_BYTE)
    return dati


def carica(percorso: Path | None = None) -> Config:
    p = percorso or percorso_predefinito()
    dati = _leggi_sicuro(p)
    if dati is None:
        return Config()
    try:
        tom = tomllib.loads(dati.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ConfigError("config.sintassi", percorso=p) from None
    sconosciute = sorted(set(tom) - set(CHIAVI))
    if sconosciute:
        raise ConfigError("config.chiave_sconosciuta", chiave=sconosciute[0])
    interfaccia = tom.get("interfaccia", "auto")
    if interfaccia != "auto" and not nome_interfaccia_valido(interfaccia):
        raise ConfigError("config.interfaccia_non_valida")
    lingua = tom.get("lingua", "it")
    if lingua not in LINGUE:
        raise ConfigError("config.lingua_non_valida", lingue=", ".join(LINGUE))
    return Config(interfaccia=interfaccia, lingua=lingua, percorso=p)


def crea(percorso: Path | None = None) -> Path:
    """Scrive il file di esempio (permessi 600, cartella 700). Non sovrascrive mai."""
    p = percorso or percorso_predefinito()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        raise ConfigError("config.esiste", percorso=p) from None
    except OSError:
        raise ConfigError("config.illeggibile", percorso=p) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(MODELLO)
    return p
