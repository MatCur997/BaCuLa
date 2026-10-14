from pathlib import Path

import bacula_shared
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


class FakeBackend:
    received: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def chat(self, session_id, message):
        FakeBackend.received.append(message)
        return bacula_shared.ChatReply(reply="Benvenuto [NOME_1]! Per quante persone?")

    def health(self):
        return {"modello_disponibile": True}

    def reset(self, session_id):
        pass


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch):
    FakeBackend.received = []
    monkeypatch.setattr(bacula_shared, "BackendClient", FakeBackend)


def test_personal_data_never_leaves_the_client():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.chat_input[0].set_value("Mi chiamo Mario Rossi, il mio cell è 333 1234567").run()

    assert FakeBackend.received == ["Mi chiamo [NOME_1], il mio cell è [TELEFONO_1]"]
    shown = [m.markdown[0].value for m in at.chat_message]
    assert shown[-1] == "Benvenuto Mario Rossi! Per quante persone?"
    assert not at.exception


def test_suggestion_buttons_send_message():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button(key="sugg1").click().run()
    assert FakeBackend.received == ["Cosa avete di vegetariano?"]
