"""Dati del ristorante (menu, orari, tavoli) e regole di calendario."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
DIETARY_FILTERS = ("vegetariano", "vegano", "senza_glutine")


def weekday_name(d: date) -> str:
    return WEEKDAYS[d.weekday()]


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _from_minutes(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class Restaurant:
    def __init__(self, data: dict):
        self.data = data
        self.name: str = data["nome"]
        self.phone: str = data["contatti"]["telefono"]
        self.max_party: int = data["prenotazioni"]["max_persone"]
        self.max_days_ahead: int = data["prenotazioni"]["giorni_anticipo_max"]
        self.tables: list[dict] = data["tavoli"]
        hours = data["orari"]
        self._closed_weekdays = {WEEKDAYS.index(g) for g in hours["giorni_chiusura"]}
        self.closed_weekdays: list[str] = list(hours["giorni_chiusura"])
        self._closed_dates = set(hours.get("chiusure_straordinarie", []))
        step = hours["intervallo_prenotazioni_minuti"]
        self.shifts: dict[str, list[str]] = {
            name: [
                _from_minutes(t)
                for t in range(_to_minutes(s["primo_arrivo"]), _to_minutes(s["ultimo_arrivo"]) + 1, step)
            ]
            for name, s in hours["turni"].items()
        }

    @classmethod
    def load(cls, path: Path) -> "Restaurant":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def is_closed(self, d: date) -> bool:
        return d.weekday() in self._closed_weekdays or d.isoformat() in self._closed_dates

    def shift_of(self, hhmm: str) -> str | None:
        for name, slots in self.shifts.items():
            if hhmm in slots:
                return name
        return None

    def calendar(self, today: date, days: int = 14) -> list[str]:
        """Righe tipo 'sabato 2026-10-17' per aiutare l'LLM con le date relative."""
        lines = []
        for i in range(days):
            d = today + timedelta(days=i)
            label = "oggi" if i == 0 else "domani" if i == 1 else ""
            closed = " (chiuso)" if self.is_closed(d) else ""
            lines.append(f"- {weekday_name(d)} {d.isoformat()}{' ' + label if label else ''}{closed}")
        return lines

    # --- informazioni per i tool -------------------------------------------------

    def info(self, topic: str = "tutto") -> dict:
        d = self.data
        hours = d["orari"]
        sections = {
            "indirizzo": {"indirizzo": d["indirizzo"]},
            "contatti": {"contatti": d["contatti"]},
            "orari": {
                "orari": {
                    # Elenco esplicito: con i soli giorni di chiusura l'LLM a volte diceva "aperti tutti i giorni".
                    "giorni_apertura": [g for g in WEEKDAYS if g not in hours["giorni_chiusura"]],
                    "giorni_chiusura": hours["giorni_chiusura"],
                    "chiusure_straordinarie": hours.get("chiusure_straordinarie", []),
                    "turni": hours["turni"],
                }
            },
            "servizi": {"servizi": d["servizi"]},
        }
        if topic in sections:
            return {"nome": d["nome"], **sections[topic]}
        result = {"nome": d["nome"], "descrizione": d["descrizione"]}
        for section in sections.values():
            result.update(section)
        return result

    def menu(self, category: str | None = None, dietary: str | None = None) -> dict:
        menu = self.data["menu"]
        if category and category not in menu:
            return {"errore": f"Categoria sconosciuta. Categorie disponibili: {', '.join(menu)}"}
        if dietary and dietary not in DIETARY_FILTERS:
            return {"errore": f"Filtro sconosciuto. Filtri disponibili: {', '.join(DIETARY_FILTERS)}"}
        result = {}
        for cat, items in menu.items():
            if category and cat != category:
                continue
            selected = [
                {
                    "nome": it["nome"],
                    "descrizione": it["descrizione"],
                    "prezzo_euro": it["prezzo"],
                    "allergeni": it["allergeni"],
                }
                for it in items
                if not dietary or it.get(dietary)
            ]
            if selected:
                result[cat] = selected
        return {"menu": result}


def parse_date(value: str) -> date:
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Data non valida: '{value}'. Usa il formato AAAA-MM-GG.")


def parse_time(value: str) -> str:
    raw = str(value).strip().lower().replace(".", ":").replace("h", ":")
    parts = [p for p in raw.split(":") if p != ""]
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        raise ValueError(f"Orario non valido: '{value}'. Usa il formato HH:MM.") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"Orario non valido: '{value}'.")
    return f"{hour:02d}:{minute:02d}"
