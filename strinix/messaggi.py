"""Testi per l'utente, separati dal codice.

Il codice usa solo chiavi (es. "verifica.titolo"); il testo vero sta in
strinix/lingue/<lingua>.json. Per tradurre Strinix basta aggiungere un file.
"""

from __future__ import annotations

import json
from importlib import resources

from strinix.terminale import pulisci

LINGUE = ("it",)
_cache: dict[str, dict[str, str]] = {}
_lingua = "it"


def carica(lingua: str) -> dict[str, str]:
    if lingua not in LINGUE:
        raise ValueError("lingua non supportata")
    if lingua not in _cache:
        testo = resources.files("strinix.lingue").joinpath(f"{lingua}.json").read_text(encoding="utf-8")
        dati = json.loads(testo)
        if not isinstance(dati, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in dati.items()):
            raise ValueError("file di lingua non valido")
        _cache[lingua] = dati
    return _cache[lingua]


def imposta_lingua(lingua: str) -> None:
    global _lingua
    carica(lingua)
    _lingua = lingua


def t(chiave: str, /, **valori: object) -> str:
    """Testo per `chiave`; i valori inseriti vengono sempre ripuliti (possono venire da fuori)."""
    modello = carica(_lingua).get(chiave)
    if modello is None:  # non dovrebbe succedere: un test controlla che ogni chiave usata esista
        return chiave
    return modello.format_map(_Valori({k: pulisci(v) for k, v in valori.items()}))


class _Valori(dict):
    """Se manca un valore il testo mostra "?" invece di far cadere il programma."""

    def __missing__(self, chiave: str) -> str:
        return "?"
