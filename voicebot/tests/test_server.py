import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bacula_shared import BackendError, ChatReply  # noqa: E402
from server import NOT_UNDERSTOOD, create_app  # noqa: E402
from speech import clean_for_speech  # noqa: E402

SESSION = "voice-session-1"


class FakeSTT:
    text = ""

    def transcribe(self, audio):
        return self.text


class FakeTTS:
    def synthesize(self, text):
        return f"WAV:{text}".encode()


class FakeBackend:
    def __init__(self):
        self.received = []
        self.fail = False

    def chat(self, session_id, message):
        if self.fail:
            raise BackendError("down")
        self.received.append(message)
        return ChatReply(reply="Perfetto [NOME_1], a presto!")

    def reset(self, session_id):
        pass

    def health(self):
        return {"status": "ok"}


@pytest.fixture
def env():
    stt, backend = FakeSTT(), FakeBackend()
    return stt, backend, TestClient(create_app(stt, FakeTTS(), backend))


def turn(client, audio=b"x" * 100):
    return client.post("/api/turn", data={"session_id": SESSION}, files={"audio": ("t.webm", audio, "audio/webm")})


def test_turn_redacts_before_backend_and_restores_reply(env):
    stt, backend, client = env
    stt.text = "Sono Luca Neri, numero 347 1234567"
    data = turn(client).json()
    assert backend.received == ["Sono [NOME_1], numero [TELEFONO_1]"]
    assert data["inviato_al_server"] == "Sono [NOME_1], numero [TELEFONO_1]"
    assert data["risposta"] == "Perfetto Luca Neri, a presto!"
    assert base64.b64decode(data["audio"]) == b"WAV:Perfetto Luca Neri, a presto!"


def test_silence_is_not_sent(env):
    stt, backend, client = env
    data = turn(client).json()
    assert data["risposta"] == NOT_UNDERSTOOD and backend.received == []


def test_backend_down_gives_spoken_apology(env):
    stt, backend, client = env
    stt.text = "ciao"
    backend.fail = True
    assert "non è raggiungibile" in turn(client).json()["risposta"]


def test_invalid_session_rejected(env):
    _, _, client = env
    r = client.post("/api/turn", data={"session_id": "../x"}, files={"audio": ("t", b"x", "audio/webm")})
    assert r.status_code == 422


def test_clean_for_speech():
    assert clean_for_speech("Il codice è **482157**, costo 12 €") == "Il codice è 4 8 2 1 5 7, costo 12 euro"
