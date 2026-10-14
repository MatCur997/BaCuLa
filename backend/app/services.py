"""Logica di prenotazione deterministica: l'LLM la invoca tramite tool, non la reimplementa."""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from typing import Callable

from .db import Database
from .restaurant import Restaurant, parse_date, parse_time, weekday_name


class BookingError(Exception):
    """Errore di dominio con messaggio comprensibile, restituito all'LLM."""


def _booking_dict(row: sqlite3.Row) -> dict:
    d = date.fromisoformat(row["date"])
    return {
        "codice": row["code"],
        "data": row["date"],
        "giorno": weekday_name(d),
        "turno": row["shift"],
        "orario": row["time"],
        "persone": row["party_size"],
        "zona": row["area"],
        "note": row["notes"],
        "stato": row["status"],
    }


_BOOKING_SELECT = """
    SELECT b.*, t.name AS table_name, t.seats, t.area
    FROM bookings b JOIN tables t ON t.id = b.table_id
"""


class BookingService:
    def __init__(self, db: Database, restaurant: Restaurant, now: Callable[[], datetime] = datetime.now):
        self.db = db
        self.restaurant = restaurant
        self._now = now

    # --- validazione ------------------------------------------------------------

    def _validate_day(self, value: str) -> date:
        try:
            d = parse_date(value)
        except ValueError as exc:
            raise BookingError(str(exc)) from None
        today = self._now().date()
        if d < today:
            raise BookingError(f"La data {d.isoformat()} è già passata.")
        if d > today + timedelta(days=self.restaurant.max_days_ahead):
            raise BookingError(
                f"Si può prenotare al massimo {self.restaurant.max_days_ahead} giorni in anticipo."
            )
        if self.restaurant.is_closed(d):
            raise BookingError(
                f"Il ristorante è chiuso {weekday_name(d)} {d.isoformat()}. "
                f"Chiusura settimanale: {', '.join(self.restaurant.closed_weekdays)}; "
                "negli altri giorni è aperto sia a pranzo sia a cena."
            )
        return d

    def _validate_time(self, d: date, value: str) -> tuple[str, str]:
        try:
            hhmm = parse_time(value)
        except ValueError as exc:
            raise BookingError(str(exc)) from None
        shift = self.restaurant.shift_of(hhmm)
        if shift is None:
            slots = "; ".join(f"{name}: {', '.join(s)}" for name, s in self.restaurant.shifts.items())
            raise BookingError(f"Orario {hhmm} non prenotabile. Orari disponibili: {slots}.")
        if datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time()) <= self._now():
            raise BookingError(f"L'orario {hhmm} di oggi è già passato.")
        return hhmm, shift

    def _validate_party(self, value) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise BookingError(f"Numero di persone non valido: '{value}'.") from None
        if n < 1:
            raise BookingError("Il numero di persone deve essere almeno 1.")
        if n > self.restaurant.max_party:
            raise BookingError(
                f"Online si prenota fino a {self.restaurant.max_party} persone. "
                f"Per gruppi più numerosi chiamare il {self.restaurant.phone}."
            )
        return n

    @staticmethod
    def _normalize_code(code) -> str:
        digits = re.sub(r"\D", "", str(code))
        if len(digits) != 6:
            raise BookingError(f"Codice prenotazione non valido: '{code}'. Il codice ha 6 cifre.")
        return digits

    # --- query ------------------------------------------------------------------

    @staticmethod
    def _free_table(conn: sqlite3.Connection, day: str, shift: str, party: int, exclude_id: int = -1):
        """Il tavolo libero più piccolo che può ospitare il gruppo."""
        return conn.execute(
            """
            SELECT * FROM tables
            WHERE seats >= ?
              AND id NOT IN (SELECT table_id FROM bookings
                             WHERE date = ? AND shift = ? AND status = 'confermata' AND id != ?)
            ORDER BY seats, id LIMIT 1
            """,
            (party, day, shift, exclude_id),
        ).fetchone()

    def _get_row(self, conn: sqlite3.Connection, code: str) -> sqlite3.Row:
        row = conn.execute(_BOOKING_SELECT + " WHERE b.code = ?", (code,)).fetchone()
        if row is None:
            raise BookingError(f"Nessuna prenotazione trovata con codice {code}.")
        return row

    def _new_code(self, conn: sqlite3.Connection) -> str:
        while True:
            code = str(100000 + secrets.randbelow(900000))
            if not conn.execute("SELECT 1 FROM bookings WHERE code = ?", (code,)).fetchone():
                return code

    # --- casi d'uso --------------------------------------------------------------

    def check_availability(self, day: str, party_size, time: str | None = None) -> dict:
        d = self._validate_day(day)
        party = self._validate_party(party_size)
        now = self._now()
        result: dict = {"data": d.isoformat(), "giorno": weekday_name(d), "persone": party, "turni": {}}
        with self.db.connect() as conn:
            for shift, slots in self.restaurant.shifts.items():
                future_slots = [
                    s for s in slots if datetime.combine(d, datetime.strptime(s, "%H:%M").time()) > now
                ]
                free = bool(future_slots) and self._free_table(conn, d.isoformat(), shift, party) is not None
                result["turni"][shift] = {"disponibile": free, "orari": future_slots if free else []}
        if time:
            hhmm, shift = self._validate_time(d, time)
            result["orario_richiesto"] = hhmm
            result["orario_richiesto_disponibile"] = result["turni"][shift]["disponibile"]
        return result

    def create_booking(self, day: str, time: str, party_size, notes: str = "", dry_run: bool = False) -> dict:
        d = self._validate_day(day)
        hhmm, shift = self._validate_time(d, time)
        party = self._validate_party(party_size)
        now = self._now().isoformat(timespec="seconds")
        with self.db.transaction(commit=not dry_run) as conn:
            table = self._free_table(conn, d.isoformat(), shift, party)
            if table is None:
                raise BookingError(
                    f"Nessun tavolo libero per {party} persone a {shift} di {weekday_name(d)} {d.isoformat()}."
                )
            code = self._new_code(conn)
            conn.execute(
                "INSERT INTO bookings (code, date, shift, time, party_size, table_id, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, d.isoformat(), shift, hhmm, party, table["id"], (notes or "").strip(), now, now),
            )
            return _booking_dict(self._get_row(conn, code))

    def get_booking(self, code) -> dict:
        code = self._normalize_code(code)
        with self.db.connect() as conn:
            return _booking_dict(self._get_row(conn, code))

    def modify_booking(self, code, day: str | None = None, time: str | None = None,
                       party_size=None, notes: str | None = None, dry_run: bool = False) -> dict:
        code = self._normalize_code(code)
        with self.db.transaction(commit=not dry_run) as conn:
            row = self._get_row(conn, code)
            if row["status"] != "confermata":
                raise BookingError(f"La prenotazione {code} è stata cancellata e non si può modificare.")
            old_start = datetime.fromisoformat(f"{row['date']}T{row['time']}")
            if old_start <= self._now():
                raise BookingError(f"La prenotazione {code} è già passata e non si può modificare.")

            d = self._validate_day(day) if day else date.fromisoformat(row["date"])
            if time:
                hhmm, shift = self._validate_time(d, time)
            else:
                hhmm, shift = self._validate_time(d, row["time"])
            party = self._validate_party(party_size) if party_size is not None else row["party_size"]
            new_notes = row["notes"] if notes is None else notes.strip()

            same_slot = d.isoformat() == row["date"] and shift == row["shift"]
            if same_slot and party <= row["seats"]:
                table_id = row["table_id"]
            else:
                table = self._free_table(conn, d.isoformat(), shift, party, exclude_id=row["id"])
                if table is None:
                    raise BookingError(
                        f"Nessun tavolo libero per {party} persone a {shift} di "
                        f"{weekday_name(d)} {d.isoformat()}. La prenotazione originale resta valida."
                    )
                table_id = table["id"]
            conn.execute(
                "UPDATE bookings SET date = ?, shift = ?, time = ?, party_size = ?, table_id = ?, notes = ?, "
                "updated_at = ? WHERE id = ?",
                (d.isoformat(), shift, hhmm, party, table_id, new_notes,
                 self._now().isoformat(timespec="seconds"), row["id"]),
            )
            return _booking_dict(self._get_row(conn, code))

    def cancel_booking(self, code, dry_run: bool = False) -> dict:
        code = self._normalize_code(code)
        with self.db.transaction(commit=not dry_run) as conn:
            row = self._get_row(conn, code)
            if row["status"] == "cancellata":
                raise BookingError(f"La prenotazione {code} era già stata cancellata.")
            conn.execute(
                "UPDATE bookings SET status = 'cancellata', updated_at = ? WHERE id = ?",
                (self._now().isoformat(timespec="seconds"), row["id"]),
            )
            return _booking_dict(self._get_row(conn, code))

    # --- dashboard ristoratore -----------------------------------------------------

    def overview(self, day: str) -> dict:
        try:
            d = parse_date(day)
        except ValueError as exc:
            raise BookingError(str(exc)) from None
        with self.db.connect() as conn:
            tables = conn.execute("SELECT * FROM tables ORDER BY seats, id").fetchall()
            rows = conn.execute(
                _BOOKING_SELECT + " WHERE b.date = ? AND b.status = 'confermata' ORDER BY b.time",
                (d.isoformat(),),
            ).fetchall()
            cancelled = conn.execute(
                "SELECT COUNT(*) FROM bookings WHERE date = ? AND status = 'cancellata'", (d.isoformat(),)
            ).fetchone()[0]
        total_seats = sum(t["seats"] for t in tables)
        shifts = {}
        for shift in self.restaurant.shifts:
            by_table = {r["table_id"]: r for r in rows if r["shift"] == shift}
            covers = sum(r["party_size"] for r in by_table.values())
            shifts[shift] = {
                "prenotazioni": len(by_table),
                "coperti": covers,
                "tavoli_occupati": len(by_table),
                "tavoli_totali": len(tables),
                "occupazione_percentuale": round(100 * covers / total_seats) if total_seats else 0,
                "tavoli": [
                    {
                        "nome": t["name"],
                        "posti": t["seats"],
                        "zona": t["area"],
                        "prenotazione": _booking_dict(by_table[t["id"]]) if t["id"] in by_table else None,
                    }
                    for t in tables
                ],
            }
        return {
            "data": d.isoformat(),
            "giorno": weekday_name(d),
            "chiuso": self.restaurant.is_closed(d),
            "cancellate": cancelled,
            "turni": shifts,
        }
