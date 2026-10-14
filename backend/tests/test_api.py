from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.agent import LLMError
from app.config import Settings
from app.main import create_app

from .conftest import BACKEND_DIR

KEY = "test-key"
SESSION = "session-0001"
# Un giorno futuro di apertura (non lunedì), indipendente dalla data di esecuzione.
DAY = next(d for d in (date.today() + timedelta(days=i) for i in range(3, 10)) if d.weekday() != 0).isoformat()


class ScriptedLLM:
    """Finto LLM: restituisce in ordine le risposte preparate e registra i messaggi ricevuti."""

    model = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools, retry=False):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def tool_call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def text(content):
    return {"role": "assistant", "content": content}


@pytest.fixture
def make_client(tmp_path):
    def _make(llm):
        settings = Settings(api_key=KEY, db_path=tmp_path / "api.db",
                            restaurant_path=BACKEND_DIR / "data" / "restaurant.json")
        return TestClient(create_app(settings, llm=llm))
    return _make


def post_chat(client, message, key=KEY):
    return client.post("/api/chat", json={"session_id": SESSION, "message": message}, headers={"X-API-Key": key})


def test_requires_api_key(make_client):
    client = make_client(ScriptedLLM())
    assert post_chat(client, "ciao", key="sbagliata").status_code == 401
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_rejects_invalid_session_id(make_client):
    client = make_client(ScriptedLLM())
    resp = client.post("/api/chat", json={"session_id": "../x", "message": "ciao"}, headers={"X-API-Key": KEY})
    assert resp.status_code == 422


def covers(client):
    ov = client.get(f"/api/admin/overview?date={DAY}", headers={"X-API-Key": KEY}).json()
    return ov["turni"]["cena"]["coperti"]


def test_booking_requires_confirmation_in_next_turn(make_client):
    create = tool_call("create_booking", date=DAY, time="20:00", party_size=3)
    llm = ScriptedLLM(
        create,                                   # turno 1: solo anteprima
        create,                                   # stesso turno: ancora anteprima
        text("Confermi la prenotazione?"),
        tool_call("create_booking", date=DAY, time="20.00", party_size="3"),  # turno 2: confermata
        text("Fatto!"),
    )
    client = make_client(llm)
    resp = post_chat(client, f"tavolo per 3 il {DAY} alle 20")
    assert resp.json() == {"reply": "Confermi la prenotazione?", "tools_used": ["create_booking", "create_booking"]}
    assert '"richiede_conferma": true' in llm.calls[1][-1]["content"]
    assert covers(client) == 0

    assert post_chat(client, "sì").json()["reply"] == "Fatto!"
    assert '"codice"' in llm.calls[-1][-1]["content"]
    assert covers(client) == 3


def test_changed_parameters_need_new_confirmation(make_client):
    llm = ScriptedLLM(
        tool_call("create_booking", date=DAY, time="20:00", party_size=3), text("Confermi?"),
        tool_call("create_booking", date=DAY, time="20:00", party_size=4), text("Confermi per 4?"),
    )
    client = make_client(llm)
    post_chat(client, "tavolo per 3")
    post_chat(client, "anzi siamo in 4")
    assert covers(client) == 0


def test_tool_errors_are_returned_to_llm(make_client):
    llm = ScriptedLLM(tool_call("cancel_booking", code="999999"), text("Codice non trovato."))
    client = make_client(llm)
    post_chat(client, "cancella 999999")
    assert "Nessuna prenotazione" in llm.calls[1][-1]["content"]


def test_history_kept_per_session_and_reset(make_client):
    llm = ScriptedLLM(text("Ciao!"), text("Certo."), text("Ciao di nuovo!"))
    client = make_client(llm)
    post_chat(client, "ciao")
    post_chat(client, "vorrei prenotare")
    assert [m["role"] for m in llm.calls[1]] == ["system", "user", "assistant", "user"]
    client.delete(f"/api/sessions/{SESSION}", headers={"X-API-Key": KEY})
    post_chat(client, "ciao")
    assert [m["role"] for m in llm.calls[2]] == ["system", "user"]


def test_llm_down_returns_503_and_history_is_clean(make_client):
    llm = ScriptedLLM(LLMError("down"), text("Eccomi"))
    client = make_client(llm)
    assert post_chat(client, "ciao").status_code == 503
    post_chat(client, "ciao")
    assert [m["role"] for m in llm.calls[1]] == ["system", "user"]


def test_dashboard_served(make_client):
    assert "Prenotazioni" in make_client(ScriptedLLM()).get("/dashboard").text


def test_availability_check_counts_as_summary(make_client):
    llm = ScriptedLLM(
        tool_call("check_availability", date=DAY, party_size=2, time="20:00"), text("C'è posto, confermi?"),
        tool_call("create_booking", date=DAY, time="20:00", party_size=2), text("Prenotato!"),
    )
    client = make_client(llm)
    post_chat(client, "tavolo per 2 alle 20")
    post_chat(client, "sì")
    assert covers(client) == 2


def test_empty_reply_is_retried(make_client):
    llm = ScriptedLLM(text(""), text("Eccomi!"))
    assert post_chat(make_client(llm), "ciao").json()["reply"] == "Eccomi!"


def test_false_success_claim_is_replaced_by_confirmation(make_client):
    llm = ScriptedLLM(
        tool_call("create_booking", date=DAY, time="20:00", party_size=2),
        text("Prenotazione registrata, a presto!"),
    )
    reply = post_chat(make_client(llm), "tavolo per 2 alle 20").json()["reply"]
    assert reply.startswith("Riepilogo:") and reply.endswith("Confermi la prenotazione?")
    assert "2 persone" in reply and "20:00" in reply
