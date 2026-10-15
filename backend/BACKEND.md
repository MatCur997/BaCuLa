# Backend BaCuLa: funzionamento tecnico

---

## 1. Ruolo del backend nel sistema

Il BE è l'unico componente che possiede lo **stato** (prenotazioni, conversazioni) e la **logica di dominio**. CHAT e VOICE sono client leggeri: pseudonimizzano il testo dell'utente, lo inviano al BE e ripristinano i dati personali nella risposta.

```
CHAT / VOICE ──HTTP JSON + X-API-Key──▶ FastAPI ──▶ Agente ──▶ Ollama (gemma4, locale)
                                           │           │
                                           │           └─ tool calling ──▶ BookingService ──▶ SQLite
                                           └─ /dashboard (ristoratore)
```

Vincoli che guidano il progetto:

- **Nessun dato personale entra nel BE.** Il testo arriva già con segnaposto (`[NOME_1]`, `[TELEFONO_1]`); l'identità del cliente è solo il **codice di prenotazione a 6 cifre**.
- **Tutti i modelli girano in locale.** L'LLM è servito da Ollama sulla stessa macchina.
- **L'LLM decide cosa fare, il codice lo fa.** Il modello non scrive SQL e non calcola disponibilità: invoca funzioni Python deterministiche e testate.

Stack: Python ≥ 3.12, FastAPI + Uvicorn, httpx, SQLite (libreria standard), python-dotenv. Le dipendenze sono in [requirements.txt](requirements.txt).

---

## 2. Mappa dei moduli

| File | Responsabilità | Dipende da |
|---|---|---|
| [`__main__.py`](app/__main__.py) | Entry point (`python -m app`): avvia Uvicorn | config |
| [`config.py`](app/config.py) | Lettura e validazione della configurazione da `.env` | — |
| [`main.py`](app/main.py) | Composizione dell'applicazione ed endpoint HTTP | tutti |
| [`sessions.py`](app/sessions.py) | Conversazioni in memoria con scadenza | agent |
| [`agent.py`](app/agent.py) | Prompt di sistema, client Ollama, ciclo di tool calling, guardrail di conferma | tools, services, restaurant |
| [`tools.py`](app/tools.py) | Schema degli strumenti esposti all'LLM e loro esecuzione | services, restaurant |
| [`services.py`](app/services.py) | Logica di prenotazione: validazioni, assegnazione tavoli, CRUD, riepilogo giornaliero | db, restaurant |
| [`restaurant.py`](app/restaurant.py) | Dati del ristorante (menu, orari, tavoli) e regole di calendario | — |
| [`db.py`](app/db.py) | Schema SQLite, connessioni e transazioni | — |
| [`static/dashboard.html`](app/static/dashboard.html) | Pagina del ristoratore | API admin |
| [`data/restaurant.json`](data/restaurant.json) | Configurazione di dominio (fittizia) | — |

Le dipendenze vanno in una sola direzione, dall'alto (HTTP) verso il basso (DB). `services.py` e `db.py` non sanno nulla di LLM o HTTP, quindi si testano in isolamento.

---

## 3. Avvio e configurazione

**File:** [`__main__.py`](app/__main__.py), [`config.py`](app/config.py), [`main.py`](app/main.py) (`get_app`, `create_app`)

Sequenza di avvio (`run.ps1` → `python -m app`):

1. `config.py` carica `backend/.env` con `load_dotenv`.
2. `Settings.from_env()` costruisce una dataclass **immutabile** (`frozen=True`). Se `API_KEY` manca, l'avvio si interrompe con un errore esplicito.
3. Uvicorn viene avviato in modalità **factory** (`app.main:get_app`, `factory=True`): la configurazione viene letta solo quando parte il server, non all'import del modulo. Così i test possono importare `create_app` senza un `.env`.
4. `create_app(settings, llm=None)` compone le dipendenze in quest'ordine:
   - `Restaurant.load(...)`: legge `restaurant.json`;
   - `Database(...).init(tables)`: crea lo schema e allinea i tavoli al JSON (upsert per nome);
   - `BookingService(db, restaurant)`;
   - `OllamaClient(...)`, a meno che venga passato un `llm` dall'esterno (i test passano un LLM finto);
   - `Agent(llm, service, restaurant)`;
   - `SessionStore(ttl)`.

Parametri configurabili:

| Variabile | Default | Uso |
|---|---|---|
| `API_KEY` | (obbligatoria) | Chiave condivisa con CHAT, VOICE e dashboard |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Ascolto in LAN |
| `DB_PATH` | `data/bacula.db` | File SQLite |
| `RESTAURANT_PATH` | `data/restaurant.json` | Dati del ristorante |
| `OLLAMA_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `gemma4:latest` | LLM |
| `OLLAMA_THINK` | `true` | Ragionamento interno del modello |
| `LLM_TEMPERATURE` | `0.2` | Bassa, per risposte e chiamate ai tool stabili |
| `LLM_TIMEOUT` | `120` s | Timeout HTTP verso Ollama |
| `SESSION_TTL_MINUTES` | `60` | Scadenza di una conversazione inattiva |

`setup.ps1` crea il virtualenv, installa le dipendenze, genera una `API_KEY` casuale (`secrets.token_urlsafe(32)`), scarica il modello con `ollama pull` e stampa l'IP da configurare sui client, insieme al comando firewall per aprire la porta 8000 alla sola subnet locale.

---

## 4. Strato HTTP

**File:** [`main.py`](app/main.py)

### Endpoint

| Metodo e percorso | Auth | Funzione |
|---|---|---|
| `GET /api/health` | no | Stato del servizio, nome del modello, disponibilità del modello su Ollama, numero di sessioni attive |
| `POST /api/chat` | sì | Un turno di conversazione |
| `DELETE /api/sessions/{id}` | sì | Azzera una conversazione (pulsante "nuova conversazione" dei client) |
| `GET /api/admin/overview?date=AAAA-MM-GG` | sì | Situazione tavoli e prenotazioni di un giorno (dashboard) |
| `GET /dashboard` | no (la pagina chiede la chiave) | Serve l'HTML statico della dashboard |

### Contratto di `/api/chat`

```json
// Richiesta
{ "session_id": "a1b2c3d4...", "message": "Vorrei prenotare per [NOME_1] sabato alle 20 in 4" }
// Risposta
{ "reply": "Riepilogo: sabato 17 ottobre alle 20:00, 4 persone. Confermi la prenotazione?",
  "tools_used": ["check_availability"] }
```

La validazione è dichiarativa (Pydantic):

- `session_id`: regex `^[A-Za-z0-9_-]{8,64}$`. È un identificativo anonimo generato dal client.
- `message`: da 1 a 2000 caratteri.

Le richieste non valide ricevono `422` da FastAPI prima di arrivare all'agente.

### Autenticazione

`require_api_key` è una dipendenza FastAPI applicata agli endpoint protetti. Confronta l'header `X-API-Key` con `secrets.compare_digest`, un confronto a tempo costante che non rivela informazioni sulla chiave tramite i tempi di risposta. Se la chiave non coincide restituisce `401`.

### Concorrenza

Gli endpoint sono funzioni sincrone (`def`), che FastAPI esegue in un thread pool, quindi più sessioni vengono servite in parallelo. All'interno della stessa sessione i turni sono serializzati da `session.lock`: se un client invia due volte lo stesso messaggio, il secondo invio aspetta il primo e la cronologia resta coerente.

### Mappatura degli errori

| Situazione | Risposta HTTP |
|---|---|
| Chiave mancante o errata | `401` |
| Payload non valido | `422` |
| Ollama irraggiungibile o in errore (`LLMError`) | `503` "Il modello linguistico non è disponibile." |
| Data non valida nella dashboard (`BookingError`) | `400` |
| Errori di dominio durante la chat (tavolo pieno, giorno di chiusura, …) | **`200`**: l'errore viene passato all'LLM, che lo spiega al cliente (vedi §7) |

---

## 5. Sessioni

**File:** [`sessions.py`](app/sessions.py)

- `SessionStore` è un dizionario `session_id → Session`, protetto da un lock globale.
- Ogni `Session` contiene la `Conversation` (cronologia e azione in attesa di conferma), il timestamp `last_seen` (`time.monotonic`, che non risente dei cambi d'ora) e un lock dedicato.
- **Scadenza:** a ogni `get()` le sessioni inattive da più di `SESSION_TTL_MINUTES` vengono eliminate. Non serve un thread di pulizia separato.
- `get()` crea la sessione se non esiste (`setdefault`), quindi il client non deve aprirla esplicitamente.

Scelta e limite: le sessioni stanno in memoria. È semplice e non persiste testo conversazionale su disco, ma un riavvio del BE chiude le conversazioni in corso. Le prenotazioni restano, perché stanno nel DB.

---

## 6. Agente conversazionale

**File:** [`agent.py`](app/agent.py)

È il blocco centrale: trasforma un messaggio in linguaggio naturale in chiamate ai servizi e in una risposta.

### 6.1 Prompt di sistema

`SYSTEM_PROMPT` viene **rigenerato a ogni chiamata** al modello con:

- nome e città del ristorante, giorno, data e ora correnti;
- un **calendario dei prossimi 14 giorni** (`Restaurant.calendar`), con "oggi", "domani" e i giorni di chiusura marcati. Serve al modello per convertire "sabato" o "domani" in `AAAA-MM-GG` senza fare calcoli;
- le regole: usare sempre gli strumenti, chiedere solo i dati mancanti, non chiedere dati personali, usare i segnaposto così come sono, rimandare al telefono i gruppi oltre 8 persone, rispondere in testo semplice (le risposte possono essere lette dal sintetizzatore vocale).

Il prompt è **volutamente breve**, con l'istruzione "chiama subito lo strumento" **in fondo**. È una scelta misurata: con `gemma4` (7.5B) un prompt lungo portava il modello ad annunciare le azioni ("verifico…") senza chiamare il tool. Su 6 scenari × 3 ripetizioni la versione attuale ha dato 18/18 chiamate corrette, contro 3/18 della versione iniziale.

### 6.2 Client LLM

`LLMClient` è un `Protocol` con il solo metodo `chat(messages, tools, retry)`. L'agente dipende da questa interfaccia, non da Ollama: nei test si inietta uno `ScriptedLLM` che restituisce risposte preparate.

`OllamaClient` chiama `POST /api/chat` di Ollama con:

- `tools`: lo schema JSON degli strumenti (§7);
- `stream: false`, `temperature: 0.2`, `keep_alive: "30m"` (il modello resta caricato in GPU tra un turno e l'altro);
- `think`: abilitato da configurazione e **invertito nel tentativo di recupero** (vedi sotto), così la seconda generazione segue un percorso diverso dalla prima.

Gli errori di rete e HTTP diventano `LLMError` e quindi `503`. `is_available()` interroga `/api/tags` per l'health check.

### 6.3 Ciclo di un turno (`_run_turn`)

```
messaggio utente ──▶ history
loop (max 6 round):
    LLM(system prompt + history, tools)
    ├─ risposta vuota (né testo né tool)  → 1 nuovo tentativo con think invertito
    ├─ tool_calls presenti                → esegui ciascun tool (via _execute),
    │                                        aggiungi i risultati a history, nuovo round
    └─ solo testo                         → controllo di coerenza sulle scritture → risposta finale
superati i 6 round                        → messaggio di fallback "puoi riformulare?"
```

Dettagli:

- `MAX_TOOL_ROUNDS = 6` limita i round per turno ed evita cicli infiniti o costosi.
- Gli argomenti dei tool vengono accettati sia come dict sia come stringa JSON. Se il JSON non è valido si passa un dict vuoto, e il tool risponde "parametro mancante".
- **Atomicità del turno:** `reply()` salva la lunghezza della cronologia all'inizio. Se il turno fallisce (es. Ollama cade a metà) la tronca, così non restano messaggi orfani.
- **Limite della cronologia:** `_trim` mantiene al massimo 40 messaggi e taglia sempre all'inizio di un turno utente, per non lasciare risultati di tool senza la chiamata corrispondente.

### 6.4 Guardrail di conferma (`_execute`)

È la principale garanzia di correttezza del sistema: **un LLM piccolo non può prenotare, modificare o cancellare senza il consenso esplicito del cliente**, anche se ignora le istruzioni del prompt. La regola è imposta dal codice, non dal prompt.

Funzionamento:

1. Gli strumenti di scrittura sono `create_booking`, `modify_booking` e `cancel_booking` (`WRITE_TOOLS`).
2. Alla **prima** chiamata il tool viene eseguito in **simulazione** (`dry_run=True`). Il risultato contiene `eseguita: false`, `richiede_conferma: true`, un riepilogo e l'istruzione esplicita di chiedere conferma. La conversazione memorizza `pending = action_key(...)` e il numero del turno.
3. La scrittura reale avviene solo se, **in un turno successivo** (quindi dopo una nuova risposta del cliente), l'LLM richiama **lo stesso strumento con gli stessi parametri**.
4. `action_key` normalizza i parametri prima del confronto (`"20.30"` = `"20:30"`, `"17/10/2026"` = `"2026-10-17"`, codice ridotto alle sole cifre) ed esclude le note, così piccole differenze di formato non costringono il cliente a riconfermare.
5. Se i parametri cambiano (es. il cliente dice "anzi, in 5"), la chiave non coincide più: parte una nuova simulazione e serve una nuova conferma.

Ottimizzazione: un `check_availability` con data, ora e persone, se l'orario richiesto è disponibile, vale già come riepilogo per `create_booking`. Il cliente risponde "sì" una volta sola, invece di confermare la disponibilità e poi di nuovo la prenotazione.

Controllo sulla risposta finale: se nel turno c'è stata una simulazione, nessuna scrittura reale e la risposta del modello non contiene una domanda (`?`), il modello ha "dimenticato" di chiedere conferma oppure sostiene di aver già registrato l'operazione. In questo caso la risposta viene **sostituita** da un riepilogo generato dal codice (`confirmation_question`), ad esempio *"Riepilogo: sabato 17 ottobre alle 20:00, 4 persone. Confermi la prenotazione?"*.

### 6.5 Esempio completo (due turni)

```
Turno 1  Utente: "Prenota sabato alle 20 per 4"
         LLM → check_availability(date=2026-10-17, time=20:00, party_size=4)
               ⇒ disponibile → pending = create_booking(2026-10-17, 20:00, 4), turno 1
         LLM → "Sabato 17 alle 20 c'è posto per 4. Confermi?"

Turno 2  Utente: "Sì"
         LLM → create_booking(date=2026-10-17, time=20:00, party_size=4)
               ⇒ chiave = pending e turno 2 > 1 → scrittura reale, codice 482913
         LLM → "Prenotazione confermata, il codice è 482913."
```

Se al turno 1 il modello avesse chiamato direttamente `create_booking`, avrebbe ricevuto solo il riepilogo simulato e nel DB non sarebbe stato scritto nulla.

---

## 7. Strumenti (tool calling)

**File:** [`tools.py`](app/tools.py)

### 7.1 Catalogo

| Tool | Parametri obbligatori | Scrive | Servizio chiamato |
|---|---|---|---|
| `check_availability` | `date`, `party_size` (+ `time` facoltativo) | no | `BookingService.check_availability` |
| `create_booking` | `date`, `time`, `party_size` (+ `notes`) | sì | `BookingService.create_booking` |
| `get_booking` | `code` | no | `BookingService.get_booking` |
| `modify_booking` | `code` (+ campi da cambiare) | sì | `BookingService.modify_booking` |
| `cancel_booking` | `code` | sì | `BookingService.cancel_booking` |
| `get_menu` | — (`category`, `dietary` facoltativi) | no | `Restaurant.menu` |
| `get_restaurant_info` | — (`topic` facoltativo) | no | `Restaurant.info` |

Gli schemi sono in formato JSON Schema (function calling compatibile con Ollama/OpenAI). Gli `enum` di `category` sono **generati dal menu** in `restaurant.json`: se si aggiunge una categoria, il tool la espone senza modifiche al codice.

### 7.2 Esecuzione (`execute_tool`)

- `_clean` rimuove gli argomenti `None` o vuoti, che i modelli piccoli tendono a passare.
- **Nessuna eccezione arriva all'LLM come stack trace.** Gli errori diventano un risultato strutturato:
  - `BookingError` → `{"ok": false, "errore": "<messaggio comprensibile>"}`;
  - `KeyError` → "Parametro obbligatorio mancante: …";
  - qualunque altra eccezione → viene registrata nel log e all'LLM arriva "Errore interno, riprova.".
- Il modello riceve l'errore come risultato del tool e lo spiega al cliente proponendo un'alternativa (regola del prompt). I messaggi di `BookingError` sono scritti apposta per questo: ad esempio indicano gli orari prenotabili o il giorno di chiusura.
- In simulazione il riepilogo di `create_booking` **non contiene il codice**, che viene generato solo alla scrittura reale. In `cancel_booking` lo stato viene mostrato come "da cancellare".

---

## 8. Logica di prenotazione

**File:** [`services.py`](app/services.py)

È il dominio puro: non conosce HTTP né LLM, riceve parametri e restituisce dict o solleva `BookingError`. L'orologio è iniettabile (`now`), quindi i test girano con una data fissa.

### 8.1 Validazioni

| Controllo | Regola |
|---|---|
| Data | Formati accettati: `AAAA-MM-GG`, `GG/MM/AAAA`, `GG-MM-AAAA`. Non nel passato, al massimo 60 giorni avanti, non in un giorno di chiusura settimanale o straordinaria |
| Orario | Normalizzato (`20`, `20.30`, `20h30` → `HH:MM`). Deve essere uno slot di arrivo di un turno; se è oggi, non deve essere già passato. L'errore elenca gli slot validi |
| Persone | Intero da 1 a 8. Oltre 8 l'errore rimanda al numero di telefono |
| Codice | Si tengono solo le cifre (`"482 913"` → `482913`); devono essere esattamente 6 |

### 8.2 Modello di capacità

- La disponibilità è gestita **per turno** (pranzo, cena): un tavolo ospita **una sola prenotazione per turno**, a prescindere dall'orario di arrivo. È un modello semplice e realistico per un'osteria, e rende verificabile il vincolo di unicità nel DB.
- Gli slot di arrivo sono generati da `restaurant.json` (primo e ultimo arrivo, passo di 30 minuti): pranzo 12:30–14:00, cena 19:30–22:00.
- **Assegnazione del tavolo** (`_free_table`): si sceglie il **tavolo libero più piccolo** con posti ≥ persone (`ORDER BY seats, id`). È una strategia best-fit: i tavoli grandi restano liberi per i gruppi.

### 8.3 Casi d'uso

- **`check_availability`**: per ogni turno indica se c'è un tavolo adatto e quali slot futuri sono prenotabili. Con un orario specifico aggiunge `orario_richiesto_disponibile`, usato dal guardrail (§6.4).
- **`create_booking`**: dentro una transazione in scrittura cerca un tavolo libero, genera il codice e inserisce la riga.
- **`get_booking`**: lettura per codice.
- **`modify_booking`**: rifiuta le prenotazioni cancellate o già passate. I campi non passati restano invariati. Se data e turno non cambiano e il tavolo ha ancora abbastanza posti, **il tavolo resta lo stesso**; altrimenti se ne cerca un altro escludendo la prenotazione stessa. Se non c'è posto, l'errore precisa che *la prenotazione originale resta valida*.
- **`cancel_booking`**: è una **cancellazione logica** (`status = 'cancellata'`): la riga resta per lo storico e il tavolo torna libero.
- **`overview`** (dashboard): per ogni turno restituisce i tavoli con l'eventuale prenotazione, i coperti, i tavoli occupati, la percentuale di occupazione (coperti / posti totali) e il numero di cancellazioni del giorno.

### 8.4 Codice di prenotazione

Il codice è un numero di 6 cifre (100000–999999) generato con `secrets.randbelow`, cioè con un generatore crittografico, quindi non prevedibile. L'unicità viene verificata nella stessa transazione dell'inserimento ed è garantita anche dal vincolo `UNIQUE` sulla colonna.

---

## 9. Dati del ristorante

**File:** [`restaurant.py`](app/restaurant.py), [`data/restaurant.json`](data/restaurant.json)

Tutta la configurazione di dominio sta in un JSON: nome, indirizzo, contatti, orari e turni, chiusure settimanali e straordinarie, limiti di prenotazione, servizi, tavoli (nome, posti, zona) e menu (con prezzi, allergeni e flag vegetariano/vegano/senza glutine). La classe `Restaurant` lo carica e offre:

- regole di calendario: `is_closed`, `shift_of(orario)`, `calendar(oggi)` per il prompt;
- `info(topic)`: restituisce solo la sezione richiesta. Gli orari includono un **elenco esplicito dei giorni di apertura**, perché con i soli giorni di chiusura il modello a volte rispondeva "aperti tutti i giorni";
- `menu(category, dietary)`: filtra per categoria e per esigenza alimentare. I valori non validi producono un errore che elenca le opzioni ammesse;
- `parse_date` / `parse_time`: parser tolleranti, condivisi tra servizi e guardrail.

Per cambiare ristorante, tavoli o menu basta modificare il JSON. All'avvio `Database.init` riallinea la tabella `tables` (upsert per nome).

---

## 10. Persistenza

**File:** [`db.py`](app/db.py)

### Schema

```sql
tables   (id, name UNIQUE, seats > 0, area)
bookings (id, code UNIQUE, date, shift ∈ {pranzo,cena}, time, party_size > 0,
          table_id → tables, notes, status ∈ {confermata,cancellata}, created_at, updated_at)

-- vincolo chiave: un tavolo non può avere due prenotazioni ATTIVE nello stesso turno
CREATE UNIQUE INDEX ux_table_per_shift ON bookings (table_id, date, shift) WHERE status = 'confermata';
CREATE INDEX ix_bookings_date ON bookings (date, shift);
```

Il **dato personale non esiste nello schema**: non ci sono colonne per nome, telefono o email.

### Garanzie contro la doppia prenotazione

La protezione è su due livelli:

1. **Transazioni `BEGIN IMMEDIATE`**: la transazione acquisisce subito il lock di scrittura, quindi due richieste concorrenti non possono leggere entrambe "tavolo libero" e poi inserire entrambe. La seconda attende (timeout di 10 s) e poi vede il tavolo occupato.
2. **Indice UNIQUE parziale**: anche in presenza di un bug applicativo, il DB rifiuta una seconda prenotazione confermata sullo stesso tavolo, data e turno. Le prenotazioni cancellate sono escluse dall'indice, quindi il tavolo si può riassegnare.

Altre impostazioni:

- `isolation_level=None`: le transazioni sono gestite esplicitamente, non dal modulo `sqlite3`.
- `PRAGMA journal_mode = WAL`: le letture (dashboard, verifiche di disponibilità) non bloccano e non vengono bloccate dalla scrittura in corso.
- `PRAGMA foreign_keys = ON`: l'integrità referenziale verso `tables` è applicata.
- Una connessione per operazione, chiusa nel `finally`. Non c'è stato condiviso tra thread.

### Simulazione tramite rollback

`transaction(commit=False)` esegue **realmente** le istruzioni e alla fine fa `ROLLBACK`. La simulazione del guardrail (§6.4) passa quindi dalle stesse validazioni, dalla stessa ricerca del tavolo e dagli stessi vincoli del DB della scrittura reale. Non c'è logica di anteprima duplicata che possa divergere da quella reale.

---

## 11. Dashboard del ristoratore

**File:** [`static/dashboard.html`](app/static/dashboard.html)

È una pagina statica senza dipendenze esterne, raggiungibile su `http://<ip-BE>:8000/dashboard`.

- Chiede l'`API_KEY` e la tiene in `sessionStorage`, che viene cancellato alla chiusura della scheda.
- Chiama `GET /api/admin/overview?date=…` con l'header `X-API-Key` e mostra per pranzo e cena i tavoli occupati sul totale, i coperti e, per ogni tavolo, l'eventuale prenotazione (codice, orario, persone, note).
- Si aggiorna ogni 30 secondi.
- I valori testuali passano da una funzione di escape HTML prima del rendering, per evitare iniezioni tramite il campo note.

---

## 12. Sicurezza e privacy

| Aspetto | Misura |
|---|---|
| Dati personali | Rimossi dai client prima dell'invio; il BE vede solo segnaposto e codici a 6 cifre; lo schema DB non ha campi anagrafici |
| Autenticazione | API key condivisa nell'header `X-API-Key`, confrontata a tempo costante; generata casualmente da `setup.ps1`; il file `.env` non è versionato |
| Rete | Porta 8000 aperta dal firewall alla sola `LocalSubnet` |
| Iniezione SQL | Solo query parametrizzate (`?`) |
| Azioni non volute dall'LLM | Guardrail di conferma imposto dal codice (§6.4) |
| Errori interni | Mai esposti al modello o al client: vengono registrati nel log, verso l'esterno va un messaggio generico |
| Input | Validazione Pydantic su `session_id` e lunghezza del messaggio |

---

## 13. Test

**Cartella:** [`tests/`](tests/). Esecuzione: `.\.venv\Scripts\python.exe -m pytest`. Stato attuale: **31 test, tutti superati**.

- [`test_services.py`](tests/test_services.py) verifica la logica di dominio con un DB temporaneo e un orologio fisso (martedì 13/10/2026 ore 10:00): creazione e lettura, normalizzazione di date, orari e codici, tutte le validazioni, assegnazione best-fit fino a esaurimento dei tavoli, modifica con mantenimento o spostamento del tavolo, fallimento della modifica senza perdita dell'originale, cancellazione che libera il tavolo, riepilogo giornaliero.
- [`test_api.py`](tests/test_api.py) verifica l'API end-to-end con un `ScriptedLLM`, senza bisogno di Ollama o GPU: obbligo della API key, rifiuto di `session_id` non validi, **conferma obbligatoria nel turno successivo**, nuova conferma se cambiano i parametri, propagazione degli errori dei tool all'LLM, isolamento e reset delle sessioni, `503` con cronologia pulita se l'LLM cade, nuovo tentativo dopo una risposta vuota, sostituzione di una falsa dichiarazione di successo.

Grazie all'iniezione di LLM e orologio i test sono deterministici e veloci (circa 2 s).

---

## 14. Limiti noti e possibili evoluzioni

| Limite | Impatto | Evoluzione |
|---|---|---|
| Sessioni in memoria | Un riavvio chiude le conversazioni aperte; un solo processo | Store esterno (es. Redis) se servono più istanze |
| Codice a 6 cifre come unica credenziale | Un milione di combinazioni, nessun limite ai tentativi | Rate limiting per sessione/IP, codice più lungo o secondo fattore |
| SQLite, un solo scrittore alla volta | Adeguato al carico di un ristorante | PostgreSQL per volumi maggiori |
| Capacità per turno, non per orario | Un tavolo non può ospitare due gruppi nello stesso turno | Gestione per fasce orarie con durata stimata del pasto |
| `/api/health` e `/dashboard` non autenticati | Espongono solo nome del modello e numero di sessioni; i dati richiedono la chiave | Autenticare anche l'health check se necessario |
| Prompt tarato su `gemma4` | Cambiando modello l'affidabilità del tool calling può peggiorare | Rieseguire la valutazione 6 scenari × 3 prima di cambiare modello o prompt |
| Turno lento tiene il lock della sessione | Un doppio invio attende fino al timeout LLM (120 s) | Accettabile: è voluto per la coerenza della cronologia |

---

## 15. In breve

Il backend è un server **FastAPI** che riceve da CHAT e VOICE messaggi già privi di dati personali e risponde come un addetto alle prenotazioni.

1. **Richiesta:** il client invia `session_id` e messaggio a `POST /api/chat` con l'header `X-API-Key`.
2. **Sessione:** la conversazione è tenuta in memoria e scade dopo 60 minuti di inattività.
3. **Agente:** un LLM locale (`gemma4` su Ollama) legge il messaggio e sceglie quale strumento chiamare: disponibilità, prenotazione, modifica, cancellazione, menu o informazioni.
4. **Strumenti → servizi:** gli strumenti invocano funzioni Python deterministiche, che validano i dati e assegnano il tavolo libero più piccolo adatto. Il modello non scrive mai SQL.
5. **Conferma obbligatoria:** ogni scrittura viene prima simulata. Viene eseguita davvero solo se, nel turno successivo, il cliente conferma e il modello ripete la stessa azione.
6. **Database:** SQLite con transazioni `BEGIN IMMEDIATE` e un indice UNIQUE che rende impossibile la doppia prenotazione dello stesso tavolo nello stesso turno.
7. **Risposta:** il testo del modello torna al client, che reinserisce i dati personali al posto dei segnaposto.

In più: il ristoratore vede l'occupazione dei tavoli su `/dashboard`, la configurazione del ristorante sta tutta in `restaurant.json` e i 31 test girano senza GPU grazie a un LLM simulato.
