# Sicurezza di Strinix

Strinix legge dati che arrivano dalla rete, quindi tratta **tutto** quello che
riceve come non fidato. Queste sono le regole che il codice segue oggi.

## Privilegi minimi e separazione dei privilegi

- Strinix non va eseguito come root, e `strinix verifica` lo segnala se succede.
- L'unico pezzo con privilegi è il **custode** (`strinix/cattura.py`): poche
  righe, solo libreria standard, avviato con `sudo` in modalità isolata
  (`python -I`, che ignora variabili d'ambiente e cartelle dell'utente).
- Il custode apre la cattura e subito dopo diventa `nobody`: niente root,
  niente gruppi, nessuna capability e `no_new_privs` attivo, quindi non può
  più riottenere privilegi. Se uno di questi passi fallisce, si ferma.
- Il custode non decodifica nulla: copia i pacchetti a Strinix, che gira come
  il tuo utente. Strinix non ha mai il socket di cattura, quindi non può
  inviare pacchetti in rete.
- Il nome dell'interfaccia è controllato sia da Strinix sia dal custode (solo
  caratteri innocui, mai un trattino iniziale) e passato a `sudo` dopo `--`.
- Strinix controlla il flusso che riceve dal custode (intestazione, lunghezze,
  tipi) e si ferma se qualcosa non torna.

### Installazione di sviluppo

Se Strinix sta in una cartella che il tuo utente può modificare (per esempio
la copia clonata da GitHub), il codice eseguito con `sudo` può essere cambiato
da quell'utente: un programma malevolo che gira come te potrebbe modificarlo e
aspettare che tu digiti la password. `strinix verifica` lo segnala. Va bene per
lo sviluppo; per l'uso quotidiano Strinix andrà installato in una cartella di
sistema, di proprietà di root.

## Input non fidato

- Ogni testo che arriva da fuori (configurazione, nomi di interfacce, nomi
  cercati nel DNS e ogni altro dato dei pacchetti) viene ripulito prima di
  essere stampato: niente caratteri di controllo, quindi niente sequenze di
  escape che possano falsare il terminale.
- La decodifica avviene in un solo punto (`strinix/decodifica.py`): un
  pacchetto che fa sbagliare Scapy viene contato come illeggibile e scartato,
  senza fermare Strinix.
- La memoria di Strinix ha limiti fissi (dispositivi, indirizzi, richieste
  DNS): chi inonda la rete di indirizzi falsi non può riempire la RAM, e
  Strinix lo segnala.
- Il file di configurazione viene letto solo se è un file normale (non un
  collegamento, una FIFO o una cartella), è tuo, non è modificabile da altri e
  pesa al massimo 16 KB. Chiavi sconosciute o valori non validi vengono rifiutati.
- `strinix config crea` scrive il file con permessi `600`, non segue
  collegamenti e non sovrascrive mai un file esistente.

## Comandi e terminale

- I comandi sono una **tabella chiusa**: nessun comando viene costruito da
  testo esterno e non si lancia mai una shell, nemmeno dalla riga dei comandi
  del cockpit (che divide il testo sugli spazi e cerca nella tabella).
- Un'opzione sconosciuta è un errore, non viene ignorata.
- Ogni testo che arriva da fuori viene ripulito prima di finire nel
  terminale: caratteri di controllo, sequenze di escape e caratteri Unicode
  che girano o nascondono il testo (bidi). Le righe vengono tagliate alla
  larghezza della finestra.
- `--json` non contiene mai sequenze di escape: i caratteri di controllo
  diventano `\uXXXX`.
- Cronologia dei comandi e stato del cockpit stanno in
  `~/.local/state/strinix/`, cartella `700` e file `600`, letti e scritti con
  le stesse regole del file di configurazione (niente collegamenti, solo file
  tuoi). Se sono rovinati, Strinix li ignora.

## Segnalare un problema

Se trovi un problema di sicurezza, apri una segnalazione privata dalla scheda
**Security** del repository GitHub ("Report a vulnerability") invece di una
issue pubblica.
