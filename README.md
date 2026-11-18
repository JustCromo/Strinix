# Strinix 🦉

Strinix osserva la tua rete e ti spiega **cosa succede, in parole semplici**.
Non è un Wireshark: invece di mostrarti migliaia di pacchetti, riconosce i
comportamenti (un dispositivo nuovo, qualcuno che si finge il router, una
scansione di porte…) e te li racconta.

Fa parte della suite **Nemirix**, insieme a Vulpnix (risk assessment) ed
Erinix (IDS sull'host). Erinix sorveglia la macchina, Strinix la rete.

> Stato: **in sviluppo** (0.1.0.dev0). Per ora c'è la base: verifica
> dell'ambiente, elenco delle interfacce e configurazione. La cattura dei
> pacchetti arriva nella prossima tappa.

## Cosa può vedere

Strinix gira sul tuo computer, quindi vede:

- **tutto il tuo traffico**;
- **i messaggi "broadcast"** di tutta la rete (ARP, DHCP, mDNS…), cioè quelli
  che i dispositivi mandano a tutti. Bastano per scoprire chi c'è in rete e
  per accorgersi di molti attacchi.

Non vede il traffico tra *altri* dispositivi (lo switch del router glielo
nasconde) e non legge il contenuto delle connessioni cifrate (HTTPS).

Usalo solo su reti tue o dove hai il permesso.

## Requisiti

- Linux
- Python 3.11 o più recente (nessuna libreria esterna, per ora)

## Uso

Dalla cartella del progetto:

```
python -m strinix verifica      # il computer è pronto? cosa manca?
python -m strinix interfacce    # schede di rete, e quale usa Strinix
python -m strinix config        # configurazione attuale
python -m strinix config crea   # scrive il file di esempio
python -m strinix aiuto
```

Opzione `--no-color` per togliere i colori (o la variabile `NO_COLOR`).

Codici di uscita: `0` tutto bene, `1` c'è qualcosa da sistemare, `2` comando
scritto male.

## Configurazione

File facoltativo `~/.config/strinix/config.toml`:

```toml
interfaccia = "auto"   # oppure "wlan0", "enp3s0"…
lingua = "it"
```

Con `"auto"` Strinix usa l'interfaccia che porta a internet.

## Test

```
python -m unittest discover -s tests -t .
```

## Licenza

MIT
