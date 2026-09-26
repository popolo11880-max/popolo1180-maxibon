# Bot affitti Pesaro → Telegram

Controlla ogni 5 minuti undici siti immobiliari e invia su Telegram i nuovi annunci residenziali trovati a Pesaro.

## Configurazione del nuovo repository

In **Settings → Secrets and variables → Actions**, crea questi due repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

GitHub non permette di visualizzare nuovamente i valori dei Secrets del vecchio repository. Recupera il token dalla chat con **@BotFather** e il chat ID dal valore che avevi salvato oppure tramite `getUpdates`. Non inserirli mai nei file pubblici.

## Test

1. Apri **Actions** e abilita i workflow se GitHub lo richiede.
2. Apri **Controlla affitti Pesaro** e premi **Run workflow**.
3. Seleziona **Invia solo un messaggio Telegram di prova** e avvia.
4. Avvia nuovamente senza selezionare il test per eseguire il primo controllo reale.

Il file `state/seen.json` contiene soltanto identificativi crittografici degli annunci già controllati. Viene aggiornato e salvato solo quando compaiono annunci mai visti; gli errori temporanei non producono più un commit ogni cinque minuti.

## Siti monitorati

- Kimia Home Immobiliare
- Agenzia Holiday Home
- MT Casa
- Immobiliare Trieste Pesaro
- Falcioni Immobiliare
- Pesaro Case
- Andreani Casa
- Bicasa
- Immobiliare.it
- Casa.it
- Idealista

Uffici, negozi, capannoni e altri immobili commerciali vengono esclusi.
