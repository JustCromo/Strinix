# Strinix 🦉

Strinix osserva la tua rete e ti spiega **cosa succede, in parole semplici**.
Non è un Wireshark: invece di mostrarti migliaia di pacchetti, riconosce i
comportamenti (un dispositivo nuovo, qualcuno che si finge il router, una
scansione di porte…) e te li racconta.

Fa parte della suite **Nemirix**, insieme a Vulpnix (risk assessment) ed
Erinix (IDS sull'host). Erinix sorveglia la macchina, Strinix la rete.

> Stato: **in sviluppo** (0.1.0.dev0). C'è la base (verifica dell'ambiente,
> interfacce, configurazione) e il primo ascolto della rete: dispositivi nuovi,
> cambi sospetti nella tabella ARP, ricerche DNS.

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

- Linux, con `sudo`
- Python 3.11 o più recente
- [Scapy](https://scapy.net) per decodificare i pacchetti (su Arch: `sudo pacman -S python-scapy`)

## Uso

Dalla cartella del progetto:

```
python -m strinix verifica      # il computer è pronto? cosa manca?
python -m strinix ascolta       # ascolta la rete e racconta cosa succede (Ctrl+C per fermare)
python -m strinix interfacce    # schede di rete, e quale usa Strinix
python -m strinix config        # configurazione attuale
python -m strinix config crea   # scrive il file di esempio
python -m strinix aiuto
```

Esempio di `ascolta`:

```
Strinix · in ascolto su wlan0
  08:42:26 ● Nuovo dispositivo: 2c:8d:b1:71:af:ed (IP 192.168.1.187) · questo computer
  08:42:26 ● Nuovo dispositivo: aa:bb:cc:00:00:01 (IP -) · router
  08:42:27 ● 192.168.1.187 cerca github.com
```

Opzione `--no-color` per togliere i colori (o la variabile `NO_COLOR`).

Codici di uscita: `0` tutto bene, `1` c'è qualcosa da sistemare, `2` comando
scritto male.

## Come funziona la cattura

Leggere i pacchetti richiede un permesso speciale del kernel (`CAP_NET_RAW`),
ma i pacchetti sono dati che chiunque in rete può costruire apposta. Per questo
Strinix è diviso in due, come Wireshark con `dumpcap`:

1. **il custode** (`strinix/cattura.py`) parte con `sudo`, apre la cattura e
   diventa subito l'utente `nobody`, senza più alcun privilegio. Poi copia i
   pacchetti a Strinix **senza leggerli**;
2. **Strinix** gira come il tuo utente, senza privilegi: decodifica i
   pacchetti con Scapy e racconta cosa succede.

Così il codice che analizza dati ostili non ha mai avuto privilegi, e non può
nemmeno spedire pacchetti in rete.

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
