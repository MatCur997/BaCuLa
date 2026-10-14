"""Strumenti esposti all'LLM (function calling) e loro esecuzione."""
from __future__ import annotations

import logging
import re
from typing import Any

from .restaurant import DIETARY_FILTERS, Restaurant, parse_date, parse_time
from .services import BookingError, BookingService

log = logging.getLogger(__name__)

# Strumenti che scrivono sul DB: richiedono sempre una conferma del cliente (vedi Agent).
WRITE_TOOLS = {"create_booking", "modify_booking", "cancel_booking"}

_CONFIRM_NOTE = (
    "Il sistema chiede sempre conferma: la prima chiamata restituisce solo un riepilogo; "
    "se il cliente conferma, richiama lo strumento con gli stessi parametri."
)


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_DATE = {"type": "string", "description": "Data in formato AAAA-MM-GG"}
_TIME = {"type": "string", "description": "Orario di arrivo in formato HH:MM"}
_PARTY = {"type": "integer", "description": "Numero di persone"}
_CODE = {"type": "string", "description": "Codice prenotazione di 6 cifre"}
_NOTES = {"type": "string", "description": "Richieste particolari (allergie, seggiolone, ...). Facoltativo"}


def tool_specs(restaurant: Restaurant) -> list[dict]:
    categories = list(restaurant.data["menu"])
    return [
        _fn(
            "check_availability",
            "Verifica se ci sono tavoli liberi in una data per un certo numero di persone. "
            "Restituisce per pranzo e cena se c'è posto e gli orari di arrivo prenotabili.",
            {"date": _DATE, "party_size": _PARTY, "time": {**_TIME, "description": "Orario desiderato (facoltativo)"}},
            ["date", "party_size"],
        ),
        _fn(
            "create_booking",
            "Crea una prenotazione e restituisce il codice prenotazione. " + _CONFIRM_NOTE,
            {"date": _DATE, "time": _TIME, "party_size": _PARTY, "notes": _NOTES},
            ["date", "time", "party_size"],
        ),
        _fn(
            "get_booking",
            "Recupera i dettagli di una prenotazione esistente dato il codice.",
            {"code": _CODE},
            ["code"],
        ),
        _fn(
            "modify_booking",
            "Modifica una prenotazione esistente. Passa solo i campi da cambiare. Verifica da solo la "
            "disponibilità: non serve chiamare prima check_availability. " + _CONFIRM_NOTE,
            {"code": _CODE, "date": _DATE, "time": _TIME, "party_size": _PARTY, "notes": _NOTES},
            ["code"],
        ),
        _fn(
            "cancel_booking",
            "Cancella una prenotazione. " + _CONFIRM_NOTE,
            {"code": _CODE},
            ["code"],
        ),
        _fn(
            "get_menu",
            "Restituisce il menu con prezzi e allergeni, eventualmente filtrato.",
            {
                "category": {"type": "string", "enum": categories, "description": "Categoria (facoltativa)"},
                "dietary": {"type": "string", "enum": list(DIETARY_FILTERS), "description": "Filtro (facoltativo)"},
            },
            [],
        ),
        _fn(
            "get_restaurant_info",
            "Informazioni sul ristorante: indirizzo e come arrivare, contatti, orari e giorni di chiusura, servizi.",
            {"topic": {"type": "string", "enum": ["indirizzo", "contatti", "orari", "servizi", "tutto"]}},
            [],
        ),
    ]


def _clean(args: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (args or {}).items() if v not in (None, "")}


def action_key(name: str, args: dict[str, Any]) -> tuple:
    """Identifica un'azione di scrittura a prescindere dal formato dei parametri ('20.30' == '20:30')."""
    def norm(key: str, value: Any) -> str:
        try:
            if key == "date":
                return parse_date(value).isoformat()
            if key == "time":
                return parse_time(value)
            if key == "code":
                return re.sub(r"\D", "", str(value))
            if key == "party_size":
                return str(int(value))
        except (TypeError, ValueError):
            pass
        return str(value).strip().lower()

    args = _clean(args)
    return (name, *sorted((k, norm(k, v)) for k, v in args.items() if k != "notes"))


def execute_tool(name: str, args: dict[str, Any], service: BookingService, restaurant: Restaurant,
                 dry_run: bool = False) -> dict:
    """Esegue un tool. Gli errori diventano {'ok': False, 'errore': ...} da spiegare al cliente.

    Con dry_run=True gli strumenti di scrittura vengono solo simulati e restituiscono un riepilogo.
    """
    args = _clean(args)
    try:
        if name == "check_availability":
            data = service.check_availability(args["date"], args["party_size"], args.get("time"))
        elif name == "create_booking":
            data = {"prenotazione": service.create_booking(
                args["date"], args["time"], args["party_size"], args.get("notes", ""), dry_run=dry_run)}
        elif name == "get_booking":
            data = {"prenotazione": service.get_booking(args["code"])}
        elif name == "modify_booking":
            data = {"prenotazione": service.modify_booking(
                args["code"], args.get("date"), args.get("time"), args.get("party_size"), args.get("notes"),
                dry_run=dry_run)}
        elif name == "cancel_booking":
            data = {"prenotazione": service.cancel_booking(args["code"], dry_run=dry_run)}
        elif name == "get_menu":
            data = restaurant.menu(args.get("category"), args.get("dietary"))
        elif name == "get_restaurant_info":
            data = restaurant.info(args.get("topic", "tutto"))
        else:
            return {"ok": False, "errore": f"Strumento sconosciuto: {name}"}
    except BookingError as exc:
        return {"ok": False, "errore": str(exc)}
    except KeyError as exc:
        return {"ok": False, "errore": f"Parametro obbligatorio mancante: {exc.args[0]}"}
    except Exception:
        log.exception("Errore inatteso nel tool %s", name)
        return {"ok": False, "errore": "Errore interno, riprova."}
    if dry_run and name in WRITE_TOOLS:
        preview = dict(data["prenotazione"])
        if name == "create_booking":
            preview.pop("codice")  # il codice definitivo viene generato solo alla conferma
        if name == "cancel_booking":
            preview["stato"] = "da cancellare"
        return {
            "ok": True,
            "eseguita": False,
            "richiede_conferma": True,
            "riepilogo": preview,
            "istruzione": "L'operazione NON è ancora stata eseguita. Non dire che è registrata, modificata "
                          "o cancellata. Riepiloga i dettagli al cliente e chiedigli se conferma. "
                          "Se conferma, richiama lo stesso strumento con gli stessi parametri.",
        }
    if name in WRITE_TOOLS:
        return {"ok": True, "eseguita": True, **data}
    return {"ok": True, **data}
