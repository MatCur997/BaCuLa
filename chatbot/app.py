"""Chat-bot di prenotazione (Streamlit). Avvio: streamlit run app.py

Ogni messaggio dell'utente viene pseudonimizzato QUI, sulla macchina CHAT, prima di
essere inviato al backend. I dati reali restano solo in memoria in questa sessione.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from bacula_shared import BackendClient, BackendError, Pseudonymizer

load_dotenv(Path(__file__).parent / ".env")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")
RESTAURANT_NAME = os.getenv("RESTAURANT_NAME", "Osteria BaCuLa")

GREETING = (
    f"Benvenuto all'{RESTAURANT_NAME}! Posso aiutarti a prenotare, modificare o cancellare "
    "un tavolo, mostrarti il menu o darti informazioni su orari e indirizzo."
)
SUGGESTIONS = [
    "Vorrei prenotare un tavolo per 2 sabato sera",
    "Cosa avete di vegetariano?",
    "Dove vi trovate e quali sono gli orari?",
    "Devo cancellare una prenotazione",
]

st.set_page_config(page_title=f"{RESTAURANT_NAME} · Prenotazioni", page_icon="🍝", layout="centered")


@st.cache_resource
def get_client() -> BackendClient:
    return BackendClient(BACKEND_URL, API_KEY)


def new_conversation() -> None:
    st.session_state.session_id = uuid.uuid4().hex
    st.session_state.pseudo = Pseudonymizer()
    st.session_state.messages = [{"role": "assistant", "content": GREETING}]
    st.session_state.sent_log = []  # ciò che è stato effettivamente inviato al server


if "session_id" not in st.session_state:
    new_conversation()

client = get_client()

# --- barra laterale ---------------------------------------------------------------
with st.sidebar:
    st.header(RESTAURANT_NAME)
    if st.button("Nuova conversazione", use_container_width=True):
        try:
            client.reset(st.session_state.session_id)
        except BackendError:
            pass
        new_conversation()
        st.rerun()

    try:
        health = client.health()
        if health.get("modello_disponibile"):
            st.success("Server di prenotazione online", icon="🟢")
        else:
            st.warning("Server online, modello AI non caricato", icon="🟡")
    except BackendError as exc:
        st.error(str(exc), icon="🔴")

    with st.expander("Privacy: cosa riceve il server"):
        st.caption(
            "Nomi, telefoni, email e altri dati personali vengono sostituiti da segnaposto "
            "su questo dispositivo, prima dell'invio."
        )
        if st.session_state.sent_log:
            for line in st.session_state.sent_log:
                st.code(line, language=None)
        else:
            st.caption("Nessun messaggio inviato.")

# --- conversazione ----------------------------------------------------------------
st.title("Prenota il tuo tavolo")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🍝"):
        st.markdown(msg["content"])

prompt = st.chat_input("Scrivi un messaggio…")
if len(st.session_state.messages) == 1 and not prompt:
    cols = st.columns(2)
    for i, suggestion in enumerate(SUGGESTIONS):
        if cols[i % 2].button(suggestion, use_container_width=True, key=f"sugg{i}"):
            prompt = suggestion

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(prompt)

    pseudo: Pseudonymizer = st.session_state.pseudo
    redacted = pseudo.redact(prompt)
    st.session_state.sent_log.append(redacted)

    with st.chat_message("assistant", avatar="🍝"):
        with st.spinner("Sto pensando…"):
            try:
                reply = pseudo.restore(client.chat(st.session_state.session_id, redacted).reply)
            except BackendError as exc:
                reply = f"⚠️ {exc} Riprova tra poco."
        st.markdown(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.rerun()
