# BaCuLa: prenotazioni AI per l'Osteria BaCuLa

Sistema per prenotare un tavolo in chat o a voce. Ha tre componenti, ciascuno sulla propria macchina Windows, collegati in LAN. Tutti i modelli AI girano in locale.

| Componente | Cartella | Cosa fa |
|---|---|---|
| **BE** (backend) | [`backend/`](backend/) | API FastAPI, agente LLM locale (Ollama), database SQLite, dashboard del ristoratore |
| **CHAT** | [`chatbot/`](chatbot/) | Chat web (Streamlit) con filtro dei dati personali |
| **VOICE** | [`voicebot/`](voicebot/) | Pagina web push-to-talk: riconoscimento vocale (Whisper), filtro dei dati personali, sintesi vocale (Piper) |
| shared | [`shared/`](shared/) | Codice comune a CHAT e VOICE: filtro dei dati personali e client delle API |

Casi d'uso coperti: prenotazione, modifica, cancellazione, consultazione del menu, richiesta di informazioni (indirizzo, contatti, orari, servizi).

## Architettura

```
 Macchina CHAT                         Macchina BE                                Macchina VOICE
┌────────────────────────┐           ┌──────────────────────────────────────┐   ┌──────────────────────────────┐
│ Streamlit (localhost)  │           │ FastAPI :8000   (X-API-Key)          │   │ Pagina web push-to-talk      │
│   │                    │   LAN     │   POST /api/chat                     │   │   │ audio (webm)             │
│   ▼                    │  HTTP     │     │                                │   │   ▼                          │
│ Filtro dati personali ─┼──────────▶│     ▼                                │◀──┼─ Whisper (STT, locale)       │
│ "Mario" → [NOME_1]     │  JSON     │   Agente ──▶ Ollama (gemma4, locale) │   │   ▼                          │
│   ▲                    │           │     │ tool calling                   │   │ Filtro dati personali        │
│ [NOME_1] → "Mario"     │◀──────────┼─    ▼                                │───┼▶ ripristino → Piper (TTS)   │
└────────────────────────┘           │   Servizi prenotazione ──▶ SQLite    │   └──────────────────────────────┘
                                     │   GET /dashboard (ristoratore)       │
                                     └──────────────────────────────────────┘
```

Un turno di conversazione funziona così:

1. Il client (CHAT o VOICE) sostituisce i dati personali con segnaposto come `[NOME_1]` o `[TELEFONO_1]`.
2. Il client invia il testo al BE.
3. Il BE passa il testo all'LLM, che sceglie quale strumento (tool) usare.
4. Il BE esegue la logica di prenotazione e risponde.
5. Il client rimette i dati reali al posto dei segnaposto e mostra o pronuncia la risposta.

## Decisioni tecniche

| # | Decisione | Pro | Contro |
|---|---|---|---|
| 1 | **LLM con tool calling.** L'LLM decide *cosa* fare; la logica di prenotazione è in codice Python testato ([services.py](backend/app/services.py)). | L'LLM non scrive SQL e non inventa disponibilità, quindi niente doppie prenotazioni. Logica testabile in isolamento. | Serve un modello che gestisca bene i tool, e il prompt va tarato sul modello (vedi decisione 8). |
| 2 | **Conferma imposta dal codice.** La prima chiamata a `create/modify/cancel_booking` è solo una simulazione con riepilogo. La scrittura avviene solo se il cliente risponde e l'LLM richiama lo stesso strumento con gli stessi parametri. | Un LLM piccolo che "dimentica" di chiedere conferma non può prenotare o cancellare al posto del cliente. Se l'LLM sostiene di aver completato un'operazione solo simulata, la sua risposta viene sostituita da un riepilogo generato dal codice. | A volte il cliente deve confermare due volte. |
| 3 | **Pseudonimizzazione sul client** ([pii.py](shared/bacula_shared/pii.py)), con regole per email, telefono, codice fiscale, IBAN, carta e nomi introdotti da frasi tipo "mi chiamo…". spaCy è opzionale. | Il BE non riceve mai dati personali. Nessun modello extra da installare. Il cliente vede comunque il proprio nome nelle risposte. | Un nome senza frase introduttiva ("prenota per Rossi") può sfuggire alle regole; con spaCy si migliora. |
| 4 | **Codice prenotazione a 6 cifre** come unica identità del cliente. | Coerente con la decisione 3. Facile da dettare a voce. | Il ristoratore vede un codice, non un nome. Un milione di combinazioni è sufficiente per una demo, non per la produzione (servirebbe un limite ai tentativi). |
| 5 | **Un solo endpoint `/api/chat`** usato sia da CHAT sia da VOICE, con la sessione mantenuta sul BE. | Client leggeri e intercambiabili; la logica non è duplicata. | Sessioni in memoria: un riavvio del BE chiude le conversazioni in corso. |
| 6 | **SQLite** con transazioni `BEGIN IMMEDIATE` e un **indice UNIQUE parziale** `(tavolo, data, turno)`. | Doppia prenotazione impossibile anche a livello di DB, nessun server DB da gestire. | Un solo scrittore alla volta. |
| 7 | **Ollama + `gemma4` (7.5B)**, con thinking attivo. | Installazione semplice, già pronto per i tool. Circa 2–5 s per turno su una GPU da 12 GB. | Il thinking aggiunge qualche secondo. Configurabile con `OLLAMA_THINK`. |
| 8 | **Prompt di sistema breve**, con l'istruzione "chiama subito lo strumento" in fondo. | Misurato su 6 scenari × 3 ripetizioni: 18/18 chiamate corrette, contro 3/18 del prompt lungo iniziale. | Va rimisurato se si cambia modello. |
| 9 | **Voce: Whisper `small` + Piper `it_IT-paola-medium`**, decodifica audio con PyAV. | Tutto locale. Su GPU la trascrizione richiede circa 1 s, la sintesi circa 0,2 s. | Su sola CPU Whisper `small` impiega circa 8 s: in quel caso conviene `WHISPER_MODEL=base`. |

## Installazione

Prerequisiti: Windows, Python ≥ 3.12 (testato con 3.14), Git. Su BE serve anche [Ollama](https://ollama.com/download).

```powershell
git clone https://github.com/MatCur997/BaCuLa.git
cd BaCuLa
```

### 1. Macchina BE

```powershell
cd backend
.\setup.ps1      # crea .venv, genera API_KEY nel .env, scarica il modello LLM, mostra l'IP
.\run.ps1
```

Lo script stampa la `API_KEY` e l'indirizzo da usare sulle altre due macchine. Se CHAT e VOICE non riescono a collegarsi, apri la porta 8000 nel firewall: il comando esatto è stampato da `setup.ps1`.

### 2. Macchina CHAT

```powershell
cd chatbot
.\setup.ps1      # poi in .env imposta BACKEND_URL=http://<IP-BE>:8000 e API_KEY
.\run.ps1        # apre http://localhost:8501
```

### 3. Macchina VOICE

```powershell
cd voicebot
.\setup.ps1 -Gpu   # -Gpu solo se c'è una GPU NVIDIA; poi imposta BACKEND_URL e API_KEY nel .env
.\run.ps1          # poi apri http://localhost:8002 (Chrome o Edge)
```

Il microfono funziona solo se la pagina è aperta **dalla macchina VOICE stessa** (`localhost`), perché i browser lo concedono solo a localhost o ai siti HTTPS.

## Dashboard del ristoratore

Si apre su `http://<IP-BE>:8000/dashboard` e chiede la `API_KEY`. Mostra, per il giorno scelto:

- prenotazioni, coperti e percentuale di posti occupati;
- la mappa dei tavoli di pranzo e cena;
- le prenotazioni cancellate.

Si aggiorna da sola ogni 30 secondi.

Menu, orari, chiusure straordinarie e tavoli si modificano in [`backend/data/restaurant.json`](backend/data/restaurant.json), poi si riavvia il BE.

## Test

```powershell
# da ciascuna cartella, con il suo .venv attivo
python -m pytest -q
```

| Suite | Test | Cosa verifica |
|---|---|---|
| `shared/tests` | 24 | Rilevamento dei dati personali, assenza di falsi positivi (codici, date, numero di persone), ripristino |
| `backend/tests` | 30 | Regole di prenotazione, assegnazione tavoli, conferma in due turni, autenticazione, recupero dagli errori dell'LLM |
| `chatbot/tests` | 2 | Interfaccia Streamlit: il backend riceve solo segnaposto |
| `voicebot/tests` | 5 | Turno vocale, silenzio, backend irraggiungibile, lettura dei codici |

Verifiche end-to-end fatte con il modello reale:

- **Conversazione completa** (prenota → conferma → modifica → conferma → cancella → conferma), controllando lo stato reale del DB dopo ogni passo.
- **Giro vocale completo**: Piper genera la voce del cliente, poi Whisper, filtro PII, BE e risposta vocale. Prenotazione creata correttamente, circa 5 s per turno su GPU.

## Limiti noti

- Le conversazioni vivono in memoria sul BE e scadono dopo 60 minuti di inattività.
- Il filtro dei dati personali è basato su regole: vedi la decisione 3.
- La prima installazione di VOICE scarica i modelli Whisper e Piper da internet; dopo, tutto funziona offline.
