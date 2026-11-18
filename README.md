# Strinix 🦉

Strinix osserva la tua rete e ti spiega **cosa succede, in parole semplici**.
Non è un Wireshark: invece di mostrarti migliaia di pacchetti, riconosce i
comportamenti (un dispositivo nuovo, qualcuno che si finge il router, una
scansione di porte…) e te li racconta.

Fa parte della suite **Nemirix**, insieme a Vulpnix (risk assessment) ed
Erinix (IDS sull'host). Erinix sorveglia la macchina, Strinix la rete.

> Stato: **in sviluppo** (0.1.0.dev0). C'è il cockpit a tutto schermo, la
> console classica e la riga di comando; Strinix riconosce dispositivi nuovi,
> cambi sospetti nella tabella ARP e ricerche DNS.

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

Un solo comando, tre modi di usarlo:

- `strinix`: apre il **cockpit**, una schermata a tutto terminale che si
  aggiorna da sola (chiede la password di `sudo` solo per aprire la cattura);
- `strinix <comando>`: esegue un comando e torna alla shell (per script e cron);
- `strinix --console` (oppure `--plain`, o un terminale "dumb"): la **console
  classica**, un prompt che scorre.

Senza installarlo, dalla cartella del progetto: `python -m strinix` (e
`python -m strinix <comando>`).

Cockpit, console e riga di comando usano la stessa tabella di comandi:

| Gruppo | Comando | Cosa fa |
|---|---|---|
| Generale | `aiuto [comando]` | elenco dei comandi, o l'uso di uno |
| | `versione` | la versione |
| Stato | `stato` | interfaccia, router e stato dell'ascolto |
| | `dispositivi`, `avvisi` | dentro il cockpit: chi c'è in rete, cosa controllare |
| Ascolto | `ascolta` | dal terminale: racconta la rete riga per riga |
| Diagnostica | `verifica` | il computer è pronto? cosa manca? |
| | `interfacce` | le schede di rete, e quale usa Strinix |
| Impostazioni | `config`, `config crea` | la configurazione |
| | `tema` | il tema e i colori che il terminale supporta |

`--json` (su `versione`, `stato`, `dispositivi`, `avvisi`, `verifica`,
`interfacce`, `config`, `tema`) dà un'uscita stabile per script e widget:
possono comparire campi nuovi, quelli esistenti non spariscono.

Codici di uscita: `0` tutto bene, `1` operazione fallita, `2` comando o
opzione sbagliati (con il suggerimento "intendevi...?").

### Il cockpit

Quattro pannelli: **Avvisi** (le cose da controllare), **Live** (tutto quello
che succede), **Dispositivi** (chi c'è in rete), **Rete** (la tua scheda, il
router, la cattura, il DNS cifrato). Su uno schermo largo, a destra restano
sempre visibili dispositivi e rete.

| Tasto | Cosa fa |
|---|---|
| `:` | scrivi un comando (Tab completa, ↑ ↓ cronologia) |
| Tab, Maiusc+Tab, `1`-`4` | cambia pannello |
| ↑ ↓, PgSu PgGiù | scegli una riga |
| Invio | schermo intero, poi apre i dettagli della riga |
| `/` | cerca nel pannello |
| `p` | mette in pausa la vista (l'ascolto continua) |
| `?` | guida |
| Esc | indietro |
| `q` | esci |

Il terminale viene sempre ripristinato, anche con Ctrl+C o se la finestra
viene chiusa. Per ora, chiudendo il cockpit si ferma anche l'ascolto: l'agente
in background arriverà nella prossima versione.

### Colori

Il tema si chiama **Bosco notturno**: colori smorzati e riposanti, ognuno con
un solo compito (ambra per dove puoi interagire, luna per i dati tecnici,
muschio, foglia secca e bacca per ok, avviso ed errore). Si adatta al
terminale: milioni di colori, 256, 16 o nessuno, e simboli ASCII se il
terminale non regge Unicode.

- `--no-color`: niente colori per questa esecuzione;
- `NO_COLOR=1`: niente colori, sempre;
- `STRINIX_COLOR=always|never|auto`: forza la scelta.

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
