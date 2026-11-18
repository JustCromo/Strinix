"""Configurazione di Strinix: ~/.config/strinix/config.toml (facoltativa).

Il file e' trattato come input non fidato: deve essere un file normale (niente link,
FIFO o dispositivi), tuo, non scrivibile da altri, piccolo, e con solo chiavi note.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from strinix import file_sicuri
from strinix.messaggi import LINGUE

# nomi interfaccia Linux: al massimo 15 caratteri (IFNAMSIZ - 1), solo caratteri innocui,
# e mai un trattino all'inizio (non deve sembrare un'opzione, es. "-h")
_NOME_INTERFACCIA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
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


def carica(percorso: Path | None = None) -> Config:
    p = percorso or percorso_predefinito()
    try:
        dati = file_sicuri.leggi(p, MAX_BYTE)
    except file_sicuri.FileNonSicuro as exc:
        raise ConfigError("config." + exc.motivo, percorso=p, massimo=MAX_BYTE) from None
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
    try:
        file_sicuri.scrivi(p, MODELLO, sovrascrivi=False)
    except file_sicuri.FileNonSicuro as exc:
        raise ConfigError("config." + exc.motivo, percorso=p) from None
    return p
