import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import Database  # noqa: E402
from app.restaurant import Restaurant  # noqa: E402
from app.services import BookingService  # noqa: E402

# Martedì 13 ottobre 2026, ore 10:00 (lunedì 19 è giorno di chiusura).
FIXED_NOW = datetime(2026, 10, 13, 10, 0)


@pytest.fixture
def restaurant():
    return Restaurant.load(BACKEND_DIR / "data" / "restaurant.json")


@pytest.fixture
def db(tmp_path, restaurant):
    database = Database(tmp_path / "test.db")
    database.init(restaurant.tables)
    return database


@pytest.fixture
def service(db, restaurant):
    return BookingService(db, restaurant, now=lambda: FIXED_NOW)
