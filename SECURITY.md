# Sicurezza di Strinix

Strinix legge dati che arrivano dalla rete, quindi tratta **tutto** quello che
riceve come non fidato. Queste sono le regole che il codice segue oggi.

## Privilegi minimi

- Strinix non va eseguito come root, e `strinix verifica` lo segnala se succede.
- Per leggere i pacchetti servirà solo il permesso del kernel `CAP_NET_RAW`,
  non tutti i poteri di root. Il modo di concederlo sarà documentato qui quando
  arriverà la cattura.

## Input non fidato

- Ogni testo che arriva da fuori (configurazione, nomi di interfacce, e poi i
  pacchetti) viene ripulito prima di essere stampato: niente caratteri di
  controllo, quindi niente sequenze di escape che possano falsare il terminale.
- Il file di configurazione viene letto solo se è un file normale (non un
  collegamento, una FIFO o una cartella), è tuo, non è modificabile da altri e
  pesa al massimo 16 KB. Chiavi sconosciute o valori non validi vengono rifiutati.
- `strinix config crea` scrive il file con permessi `600`, non segue
  collegamenti e non sovrascrive mai un file esistente.

## Segnalare un problema

Se trovi un problema di sicurezza, apri una segnalazione privata dalla scheda
**Security** del repository GitHub ("Report a vulnerability") invece di una
issue pubblica.
