import pytest

from app.services import BookingError


def test_create_and_get(service):
    b = service.create_booking("2026-10-17", "20:00", 4, "un seggiolone")
    assert b["turno"] == "cena" and b["persone"] == 4 and b["giorno"] == "sabato"
    assert len(b["codice"]) == 6
    assert service.get_booking(b["codice"])["note"] == "un seggiolone"


def test_time_formats_are_normalized(service):
    assert service.create_booking("17/10/2026", "20.30", 2)["orario"] == "20:30"
    assert service.create_booking("2026-10-17", "13", 2)["orario"] == "13:00"


@pytest.mark.parametrize(
    "day, time, party, error",
    [
        ("2026-10-12", "20:00", 2, "già passata"),
        ("2026-10-19", "20:00", 2, "chiuso"),
        ("2026-10-21", "20:00", 2, "chiuso"),
        ("2027-06-01", "20:00", 2, "al massimo"),
        ("2026-10-17", "17:00", 2, "non prenotabile"),
        ("2026-10-13", "09:00", 2, "non prenotabile"),
        ("2026-10-17", "20:00", 9, "gruppi"),
        ("2026-10-17", "20:00", 0, "almeno 1"),
        ("domani", "20:00", 2, "Data non valida"),
    ],
)
def test_validation(service, day, time, party, error):
    service.restaurant._closed_dates.add("2026-10-21")  # chiusura straordinaria
    with pytest.raises(BookingError, match=error):
        service.create_booking(day, time, party)


def test_smallest_table_assigned_until_full(service, restaurant):
    tables_for_2 = [t for t in restaurant.tables if t["posti"] >= 2]
    codes = [service.create_booking("2026-10-17", "20:00", 2)["codice"] for _ in tables_for_2]
    assert len(set(codes)) == len(tables_for_2)
    with pytest.raises(BookingError, match="Nessun tavolo libero"):
        service.create_booking("2026-10-17", "20:00", 2)
    # Il pranzo dello stesso giorno resta libero.
    assert service.check_availability("2026-10-17", 2)["turni"]["pranzo"]["disponibile"] is True


def test_large_group_uses_large_table(service):
    service.create_booking("2026-10-17", "20:00", 8)
    with pytest.raises(BookingError, match="Nessun tavolo libero per 7"):
        service.create_booking("2026-10-17", "21:00", 7)


def test_availability_hides_past_slots_today(service):
    avail = service.check_availability("2026-10-13", 2)
    assert avail["turni"]["pranzo"]["orari"][0] == "12:30"
    assert avail["turni"]["cena"]["disponibile"] is True


def test_modify_keeps_table_or_moves(service):
    b = service.create_booking("2026-10-17", "20:00", 2)
    assert service.modify_booking(b["codice"], time="21:00")["orario"] == "21:00"
    moved = service.modify_booking(b["codice"], day="2026-10-18", time="13:00", party_size=6)
    assert (moved["data"], moved["turno"], moved["persone"]) == ("2026-10-18", "pranzo", 6)


def test_modify_failure_keeps_original(service):
    service.create_booking("2026-10-17", "20:00", 8)
    b = service.create_booking("2026-10-17", "20:00", 2)
    with pytest.raises(BookingError, match="resta valida"):
        service.modify_booking(b["codice"], party_size=8)
    assert service.get_booking(b["codice"])["persone"] == 2


def test_cancel_frees_table(service):
    b = service.create_booking("2026-10-17", "20:00", 8)
    assert service.cancel_booking(b["codice"])["stato"] == "cancellata"
    with pytest.raises(BookingError, match="già stata cancellata"):
        service.cancel_booking(b["codice"])
    with pytest.raises(BookingError, match="cancellata"):
        service.modify_booking(b["codice"], party_size=2)
    service.create_booking("2026-10-17", "20:00", 8)


def test_code_is_normalized(service):
    code = service.create_booking("2026-10-17", "20:00", 2)["codice"]
    spaced = " ".join(code)
    assert service.get_booking(spaced)["codice"] == code
    with pytest.raises(BookingError, match="6 cifre"):
        service.get_booking("12")


def test_overview(service):
    service.create_booking("2026-10-17", "20:00", 4)
    service.create_booking("2026-10-17", "13:00", 2)
    ov = service.overview("2026-10-17")
    assert ov["turni"]["cena"]["coperti"] == 4
    assert ov["turni"]["pranzo"]["prenotazioni"] == 1
    assert service.overview("2026-10-19")["chiuso"] is True
