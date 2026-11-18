"""La console classica: un prompt che scorre, per chi la preferisce o per i terminali "poveri".

Usa la stessa tabella dei comandi del cockpit e della riga di comando.
"""

from __future__ import annotations

import sys

from strinix import cronologia
from strinix.comandi.registro import Contesto, Registro
from strinix.messaggi import t
from strinix.ui.tema import Tema


def esegui(registro: Registro, tema: Tema, *, leggi=input, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    storia = cronologia.carica()
    try:
        import readline  # frecce e cronologia, se disponibile
        for riga in storia:
            readline.add_history(riga)
    except ImportError:
        pass
    print(tema("accento", f"{tema.simbolo('gufo')} strinix") + " " + tema("tenue", t("console.benvenuto")), file=out)
    prompt = f"strinix{tema.simbolo('freccia')} "
    while True:
        try:
            riga = leggi(prompt)
        except EOFError:
            print(file=out)
            break
        except KeyboardInterrupt:
            print(file=out)
            continue
        if not riga.strip():
            continue
        storia = cronologia.aggiungi(storia, riga)
        ctx = Contesto(tema=tema, out=out, err=err, interattivo=True)
        try:
            registro.esegui(ctx, riga.split())
        except KeyboardInterrupt:
            print(file=out)
        if ctx.uscita:
            break
    cronologia.salva(storia)
    return 0
