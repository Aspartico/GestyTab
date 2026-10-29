# GestyTab – Ricerca nelle note di Arca Evolution

Programma per Windows che cerca una parola o stringa nelle **note dei documenti (fatture, ecc.) di clienti e fornitori** di Arca Evolution (database SQL Server). Funziona in sola lettura: non modifica nulla in Arca.

## Installazione
1. Scarica `GestyTab.exe` dall'artefatto della GitHub Action "Build Windows exe" (oppure esegui `build.bat` su un PC con Python).
2. Serve il driver **ODBC Driver 17/18 for SQL Server** (gratuito Microsoft) sul PC.
3. Copia l'exe dove vuoi e avvialo: nessuna installazione.

## Uso
1. All'avvio indica server SQL (es. `NOMEPC\SQLEXPRESS`), database dell'azienda Arca e tipo di accesso, poi **Connetti**.
2. Scrivi il testo, scegli **Clienti / Fornitori / Entrambi**, opzionalmente il periodo, e premi **Cerca**.
3. Doppio clic su una riga = testo completo della nota. **Esporta CSV** per Excel.
4. **Campi…** permette di scegliere in quali colonne cercare (default: tutte quelle con "nota/note" nel nome, di testata `DOTes` e di riga `DORig`).

Le impostazioni sono salvate in `gestytab.json` accanto all'exe (la password non viene salvata).

## Note
- Ricerca senza distinzione maiuscole/minuscole e accenti.
- Se la tua versione di Arca usa nomi diversi per tabelle/colonne, modificali nelle costanti all'inizio di `arca_search.py`.
- Consigliato usare un utente SQL con soli permessi di lettura.
