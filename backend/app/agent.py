"""Agente conversazionale: LLM locale (Ollama) + tool calling sui servizi di prenotazione."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

import httpx

from .restaurant import Restaurant, weekday_name
from .services import BookingService
from .tools import WRITE_TOOLS, action_key, execute_tool, tool_specs

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
          "settembre", "ottobre", "novembre", "dicembre"]
MAX_HISTORY_MESSAGES = 40

SYSTEM_PROMPT = """Sei l'assistente virtuale per le prenotazioni dell'{name} ({city}).
Rispondi sempre in italiano, in modo cordiale e conciso (2-4 frasi), in testo semplice senza markdown:
le tue risposte possono essere lette ad alta voce.

Oggi è {weekday} {today}, sono le {now}.
Calendario dei prossimi giorni (usalo per convertire "domani", "sabato", ecc. in AAAA-MM-GG):
{calendar}

Regole:
- Usa sempre gli strumenti per disponibilità, prenotazioni, menu e informazioni; non inventare nulla.
- Per prenotare servono data, orario e persone; chiedi solo ciò che manca. Prima di create_booking o cancel_booking chiedi conferma.
- Dopo la prenotazione comunica il codice di 6 cifre: serve per modificarla o cancellarla.
- Non chiedere dati personali. I segnaposto come [NOME_1] vanno usati così come sono.
- Gruppi oltre {max_party} persone: invita a telefonare al {phone}.
- Se uno strumento restituisce un errore, spiegalo in parole semplici e proponi un'alternativa.
- Per richieste estranee al ristorante, riporta gentilmente la conversazione sulle prenotazioni.

IMPORTANTE: quando ti serve un'informazione o devi fare un'operazione, chiama subito lo strumento
in questo stesso turno. Non scrivere mai frasi come "verifico", "controllo" o "procedo" senza chiamarlo.
Un'operazione è avvenuta solo se lo strumento risponde "eseguita": true."""



class LLMError(Exception):
    """Il modello linguistico non è raggiungibile o ha risposto in modo inatteso."""


class LLMClient(Protocol):
    def chat(self, messages: list[dict], tools: list[dict], retry: bool = False) -> dict:
        """Restituisce il messaggio dell'assistente: {'role', 'content', 'tool_calls'?}.

        retry=True indica un nuovo tentativo dopo una risposta vuota.
        """


class OllamaClient:
    def __init__(self, base_url: str, model: str, temperature: float = 0.2,
                 think: bool = False, timeout: float = 120.0):
        self.model = model
        self._temperature = temperature
        self._think = think
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def chat(self, messages: list[dict], tools: list[dict], retry: bool = False) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            # Nel nuovo tentativo si inverte il "thinking": cambia il percorso di generazione.
            "think": self._think != retry,
            "keep_alive": "30m",
            "options": {"temperature": self._temperature},
        }
        try:
            resp = self._http.post("/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama non raggiungibile o in errore: {exc}") from exc
        return resp.json()["message"]

    def is_available(self) -> bool:
        try:
            tags = self._http.get("/api/tags", timeout=3).json()
        except (httpx.HTTPError, ValueError):
            return False
        return any(m.get("name") == self.model for m in tags.get("models", []))


@dataclass
class Conversation:
    """Stato di una conversazione: cronologia + eventuale azione in attesa di conferma."""
    history: list[dict] = field(default_factory=list)
    turn: int = 0
    pending: tuple | None = None
    pending_turn: int = -1


@dataclass
class AgentResult:
    reply: str
    tools_used: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, llm: LLMClient, service: BookingService, restaurant: Restaurant,
                 now: Callable[[], datetime] = datetime.now):
        self.llm = llm
        self.service = service
        self.restaurant = restaurant
        self._tools = tool_specs(restaurant)
        self._now = now

    def system_prompt(self) -> str:
        now = self._now()
        r = self.restaurant
        return SYSTEM_PROMPT.format(
            name=r.name,
            city=r.data["indirizzo"]["citta"],
            weekday=weekday_name(now.date()),
            today=now.date().isoformat(),
            now=now.strftime("%H:%M"),
            calendar="\n".join(r.calendar(now.date())),
            max_party=r.max_party,
            phone=r.phone,
        )

    def reply(self, conv: Conversation, user_message: str) -> AgentResult:
        """Esegue un turno. La conversazione viene aggiornata in place (senza il prompt di sistema)."""
        start = len(conv.history)
        try:
            return self._run_turn(conv, user_message)
        except Exception:
            del conv.history[start:]  # turno fallito: non lasciare messaggi a metà nella cronologia
            raise

    def _execute(self, conv: Conversation, name: str, args: dict) -> dict:
        """Esegue un tool. Le scritture avvengono solo se confermate in un turno successivo.

        Guardrail deterministico: non ci si affida al prompt perché l'LLM chieda conferma.
        La prima chiamata a create/modify/cancel è una simulazione che restituisce un riepilogo;
        la scrittura vera avviene solo se, dopo una nuova risposta del cliente, l'LLM richiama
        lo stesso strumento con gli stessi parametri.
        Una verifica di disponibilità con data, orario e persone vale come riepilogo per create_booking:
        se il cliente risponde "sì", la prenotazione viene creata senza chiedere conferma due volte.
        """
        if name not in WRITE_TOOLS:
            result = execute_tool(name, args, self.service, self.restaurant)
            if name == "check_availability" and result.get("orario_richiesto_disponibile"):
                proposal = {"date": args.get("date"), "time": args.get("time"), "party_size": args.get("party_size")}
                conv.pending, conv.pending_turn = action_key("create_booking", proposal), conv.turn
            return result
        key = action_key(name, args)
        if conv.pending == key and conv.pending_turn < conv.turn:
            conv.pending = None
            return execute_tool(name, args, self.service, self.restaurant)
        result = execute_tool(name, args, self.service, self.restaurant, dry_run=True)
        if result.get("ok"):
            conv.pending, conv.pending_turn = key, conv.turn
        return result

    def _run_turn(self, conv: Conversation, user_message: str) -> AgentResult:
        history = conv.history
        conv.turn += 1
        history.append({"role": "user", "content": user_message})
        tools_used: list[str] = []
        previews: list[tuple[str, dict]] = []  # anteprime di scritture non ancora confermate
        executed_write = False
        empty_retries = 1
        retrying = False
        for _ in range(MAX_TOOL_ROUNDS):
            messages = [{"role": "system", "content": self.system_prompt()}, *history]
            msg = self.llm.chat(messages, self._tools, retry=retrying)
            retrying = False
            calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()
            if not calls and not content and empty_retries:
                # Capita (raramente) che il modello non produca né testo né una chiamata valida.
                log.warning("Risposta vuota dal modello, nuovo tentativo")
                empty_retries -= 1
                retrying = True
                continue
            history.append({"role": "assistant", "content": content,
                            **({"tool_calls": calls} if calls else {})})
            if not calls:
                reply = content or "Scusi, può ripetere?"
                if previews and not executed_write and "?" not in reply:
                    # L'LLM non ha chiesto conferma (o ha detto che l'operazione è fatta): la risposta
                    # viene sostituita da un riepilogo generato dal codice.
                    reply = confirmation_question(*previews[-1])
                    history[-1]["content"] = reply
                _trim(history)
                return AgentResult(reply=reply, tools_used=tools_used)
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = self._execute(conv, name, args)
                if result.get("richiede_conferma"):
                    previews.append((name, result["riepilogo"]))
                elif result.get("eseguita"):
                    executed_write = True
                log.info("tool %s(%s) -> ok=%s", name, args, result.get("ok"))
                tools_used.append(name)
                history.append({"role": "tool", "tool_name": name,
                                "content": json.dumps(result, ensure_ascii=False)})
        _trim(history)
        return AgentResult(
            reply="Mi dispiace, non sono riuscito a completare la richiesta. Puoi riformularla?",
            tools_used=tools_used,
        )


def confirmation_question(tool: str, b: dict) -> str:
    _, m, d = (int(x) for x in b["data"].split("-"))
    when = f"{b['giorno']} {d} {MONTHS[m - 1]} alle {b['orario']}, {b['persone']} persone"
    if tool == "create_booking":
        return f"Riepilogo: {when}. Confermi la prenotazione?"
    if tool == "modify_booking":
        return f"La prenotazione {b['codice']} diventerebbe: {when}. Confermi la modifica?"
    return f"Vuoi davvero cancellare la prenotazione {b['codice']} di {when}?"


def _trim(history: list[dict]) -> None:
    """Mantiene la cronologia entro un limite, tagliando sempre all'inizio di un turno utente."""
    if len(history) <= MAX_HISTORY_MESSAGES:
        return
    cut = len(history) - MAX_HISTORY_MESSAGES
    while cut < len(history) and history[cut]["role"] != "user":
        cut += 1
    del history[:cut]
