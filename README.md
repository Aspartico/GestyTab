# ArcaNote – Ricerca nelle note di Arca Evolution

Programma per Windows che cerca una parola o stringa nelle **note dei documenti (fatture, ecc.) di clienti e fornitori** di Arca Evolution (database SQL Server). Funziona in sola lettura: non modifica nulla in Arca.

## Installazione
1. Scarica l'artefatto "ArcaNote-windows" della GitHub Action (oppure esegui `build.bat` su un PC con Python). Contiene:
   - `ArcaNote-Setup.exe`: **installer** (icona sul desktop e nel menu Start, disinstallazione da Windows, nessun diritto di amministratore);
   - `ArcaNote.exe`: versione **portable**, senza installazione.
2. Serve il driver **ODBC Driver 17/18 for SQL Server** (gratuito Microsoft) sul PC.
3. Avvia l'installer e segui la procedura (oppure copia l'exe portable dove vuoi).

## Uso
1. All'avvio indica server SQL (es. `NOMEPC\SQLEXPRESS`), database dell'azienda Arca e tipo di accesso, poi **Connetti**.
2. Scrivi il testo, scegli **Clienti / Fornitori / Entrambi**, opzionalmente il periodo, e premi **Cerca**.
3. Doppio clic su una riga = testo completo della nota. **Esporta CSV** per Excel.
4. **Campi…** permette di scegliere in quali colonne cercare. Di default: le note della scheda anagrafica cliente/fornitore (`CF`) e le note di testata (`DOTes`) e di riga (`DORig`) dei documenti. Con un filtro per periodo l'anagrafica non viene cercata (non ha una data).

Le impostazioni sono salvate in `arcanote.json` accanto all'exe (la password non viene salvata).

## Note
- Ricerca senza distinzione maiuscole/minuscole e accenti.
- Se la tua versione di Arca usa nomi diversi per tabelle/colonne, modificali nelle costanti all'inizio di `arcanote.py`.
- Consigliato usare un utente SQL con soli permessi di lettura.

## Nuovo documento da richiesta scritta o a voce (fase 1: anteprima)
Pulsante **Nuovo documento…**: scrivi (o detta con Win+H) una richiesta come *"DDT per Rossi Mario, 10 batterie 12V"*.
Claude la interpreta, ArcaNote cerca cliente e articoli nel database (solo lettura) e mostra un'**anteprima modificabile**:
puoi cambiare tipo documento, intestatario, articoli, quantità e prezzi, poi stampare/salvare in PDF l'anteprima.

- **Non scrive nulla in Arca**: l'invio ad Arca è la fase 2, da provare prima sulla ditta di prova (ADB_Demo).
- Serve una **chiave API Anthropic** (si inserisce la prima volta; salvata cifrata per l'utente Windows). Al servizio vengono inviati solo il testo della richiesta e l'elenco dei tipi documento, **non** i dati del database.
- Nomi di tabelle usate in sola lettura: `DO` (tipi documento), `CF` (clienti/fornitori), `AR` (articoli).
