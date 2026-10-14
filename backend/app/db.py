"""Accesso a SQLite: schema e connessioni."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS tables (
    id    INTEGER PRIMARY KEY,
    name  TEXT    NOT NULL UNIQUE,
    seats INTEGER NOT NULL CHECK (seats > 0),
    area  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL UNIQUE,
    date       TEXT    NOT NULL,                 -- AAAA-MM-GG
    shift      TEXT    NOT NULL CHECK (shift IN ('pranzo', 'cena')),
    time       TEXT    NOT NULL,                 -- HH:MM
    party_size INTEGER NOT NULL CHECK (party_size > 0),
    table_id   INTEGER NOT NULL REFERENCES tables(id),
    notes      TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'confermata' CHECK (status IN ('confermata', 'cancellata')),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

-- Garanzia a livello di DB: un tavolo non può avere due prenotazioni attive nello stesso turno.
CREATE UNIQUE INDEX IF NOT EXISTS ux_table_per_shift
    ON bookings (table_id, date, shift) WHERE status = 'confermata';

CREATE INDEX IF NOT EXISTS ix_bookings_date ON bookings (date, shift);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def init(self, tables: list[dict]) -> None:
        """Crea lo schema e allinea i tavoli a quelli definiti in restaurant.json."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            for t in tables:
                conn.execute(
                    "INSERT INTO tables (name, seats, area) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET seats = excluded.seats, area = excluded.area",
                    (t["nome"], t["posti"], t["zona"]),
                )
            conn.execute("COMMIT")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None: le transazioni si aprono esplicitamente con BEGIN IMMEDIATE.
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, commit: bool = True) -> Iterator[sqlite3.Connection]:
        """Transazione in scrittura: blocca altri scrittori finché non termina.

        Con commit=False le modifiche vengono annullate alla fine (simulazione).
        """
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT" if commit else "ROLLBACK")
