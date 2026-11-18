# Strinix 🦉

Strinix osserva la tua rete e ti spiega **cosa succede, in parole semplici**.
Non è un Wireshark: invece di mostrarti migliaia di pacchetti, riconosce i
comportamenti (un dispositivo nuovo, qualcuno che si finge il router, una
scansione di porte…) e te li racconta.

Fa parte della suite **Nemirix**, insieme a Vulpnix (risk assessment) ed
Erinix (IDS sull'host). Erinix sorveglia la macchina, Strinix la rete.

> Stato: **in sviluppo** (0.1.0.dev0). C'è il cockpit a tutto schermo, la
> console classica e la riga di comando. Strinix riconosce i dispositivi (con
> produttore e nome), chi si collega a cosa, il traffico non cifrato e le
> password che passano in chiaro, i cambi sospetti nella tabella ARP.

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

Funziona come quello di Erinix, su tre livelli:

1. **la panoramica**: tutti i pannelli insieme, numerati. Quello scelto ha il
   bordo color ambra. A sinistra **1 Avvisi** e **2 Live**, a destra
   **3 Dispositivi**, **4 In chiaro** e **5 Rete**;
2. **Invio**: il pannello scelto a tutto schermo, con più colonne;
3. **Invio di nuovo**: la pagina della riga scelta, con *Cosa significa*,
   *Cosa fare*, i dettagli e *Cosa è successo*. Il testo va a capo e si scorre
   con le frecce.

| Pannello | Cosa mostra |
|---|---|
| **1 Avvisi** | le cose da controllare: un IP che cambia proprietario, password in chiaro |
| **2 Live** | cosa succede: chi si collega a cosa (`questo computer → github.com · HTTPS`), ricerche DNS, dispositivi nuovi e i loro nomi |
| **3 Dispositivi** | una scheda per ogni apparecchio: nome, produttore, IP, MAC, traffico, se è online, a cosa si collega |
| **4 In chiaro** | i servizi usati senza cifratura (HTTP, FTP, Telnet, posta) e se è passata una password |
| **5 Rete** | la tua scheda, il router, la cattura, il DNS cifrato; a tutto schermo anche chi usa di più la rete e i siti più usati |

In alto, due righe: una frase sulla situazione ("tutto tranquillo", "2 cose
da controllare", "1 password in chiaro!") e i numeri principali con il grafico
dell'attività dell'ultimo minuto. Lo sfondo non viene dipinto: resta quello
del tuo terminale. Sulle finestre strette o basse si vede un pannello alla
volta, con le schede in alto.

| Tasto | Cosa fa |
|---|---|
| `:` | scrivi un comando (Tab completa, ↑ ↓ cronologia) |
| Tab, Maiusc+Tab, `1`-`5` | cambia pannello |
| ↑ ↓, PgSu PgGiù | scegli una riga |
| Invio | a tutto schermo, poi la pagina della riga scelta |
| `c` | copia la riga scelta negli appunti |
| `o` | ordina i dispositivi: comparsa, traffico, nome, IP |
| `/` | cerca nel pannello |
| `p` | mette in pausa la vista (l'ascolto continua) |
| `?` | guida |
| Esc | indietro |
| `q` | esci |

Il testo si può anche selezionare con il mouse: il cockpit riscrive solo le
righe che cambiano, quindi la selezione resta (in alcuni terminali serve tenere
premuto Maiusc). Il tasto `c` usa la sequenza OSC 52, supportata dalla maggior
parte dei terminali moderni (kitty, Alacritty, foot, WezTerm, xterm...).

Dentro il cockpit ci sono solo i comandi che servono lì (`aiuto`, `stato`,
`dispositivi`, `avvisi`, `esci`); quelli di sistema (`verifica`, `interfacce`,
`config`, `tema`, `versione`) si usano dal terminale.

Il terminale viene sempre ripristinato, anche con Ctrl+C o se la finestra
viene chiusa. Per ora, chiudendo il cockpit si ferma anche l'ascolto: l'agente
in background arriverà nella prossima versione.

### Cosa rileva

Solo cose che il tuo portatile vede davvero (il tuo traffico e i messaggi che
i dispositivi mandano a tutti), con soglie prudenti per non dare falsi allarmi:

| Rilevatore | Quando scatta | Gravità |
|---|---|---|
| **Scansione della rete** | un dispositivo cerca 20+ indirizzi diversi in 10 secondi (ARP) | avviso, grave da 100; solo informazione se è il router o questo computer |
| **Scansione delle porte** | qualcuno bussa a 15+ porte di questo computer in 10 secondi | avviso, grave da 100 |
| **Qualcuno si spaccia per il router** | l'IP del router risponde da un altro MAC | grave |
| **Un MAC con tanti IP** | lo stesso dispositivo rivendica 3+ indirizzi | grave |
| **IP che rimbalza** | un indirizzo passa avanti e indietro tra due MAC | grave |
| **IP riassegnato** | un indirizzo passa a un altro dispositivo, ma il vecchio è sparito da 10 minuti | solo informazione |
| **DHCP falso** | un secondo server distribuisce indirizzi e propone un router diverso da quello vero | grave (avviso se non propone un router) |
| **DNS lunghissimi** | nomi oltre 100 caratteri: possibile DNS tunneling | avviso |
| **Domini casuali** | nomi come `xjw9q2kz8fpl.com`, tipici dei malware | avviso |
| **Password in chiaro** | una password passa senza cifratura (Strinix non la legge) | grave |

Ogni avviso ha la sua pagina con *Cosa significa* e *Cosa fare*. Lo stesso
avviso non si ripete prima di 5 minuti, salvo che diventi più grave.

### Come riconosce le cose

- **Produttore**: dai primi 3 byte del MAC, con l'elenco che Scapy si porta
  dietro (lo stesso di Wireshark), senza internet. I MAC casuali degli
  smartphone non hanno produttore.
- **Nome**: quello che il dispositivo dichiara da solo in rete (DHCP, mDNS),
  per esempio `iPhone-di-Ivan`.
- **Siti**: dalle risposte DNS e, per HTTPS, dal nome del sito che il browser
  manda in chiaro all'inizio della connessione (SNI). Il contenuto resta cifrato.
- **Traffico in chiaro**: riconosce HTTP, FTP, Telnet, POP3, IMAP e SMTP senza
  cifratura. Se passa una password se ne accorge, ma **non la legge, non la
  salva e non la mostra mai**.

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
