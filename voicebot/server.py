"""Voice-bot di prenotazione: pagina web push-to-talk + STT/TTS locali.

Avvio: python server.py  ->  aprire http://localhost:8002 su QUESTA macchina
(il browser consente il microfono solo su localhost o HTTPS).

Flusso di un turno: audio -> trascrizione (Whisper) -> pseudonimizzazione -> backend
-> ripristino dei dati reali -> sintesi vocale (Piper) -> audio al browser.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from bacula_shared import BackendClient, BackendError, Pseudonymizer
from speech import SpeechToText, make_tts

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
log = logging.getLogger("voicebot")

RESTAURANT_NAME = os.getenv("RESTAURANT_NAME", "Osteria BaCuLa")
GREETING = (
    f"Buongiorno, sono l'assistente dell'{RESTAURANT_NAME}. Posso prenotare, modificare o cancellare "
    "un tavolo, oppure darti informazioni sul menu e sugli orari. Come posso aiutarti?"
)
NOT_UNDERSTOOD = "Scusa, non ho capito. Puoi ripetere?"
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def create_app(stt, tts, backend) -> FastAPI:
    app = FastAPI(title="Voice-bot prenotazioni")
    pseudonymizers: dict[str, Pseudonymizer] = {}
    lock = threading.Lock()
    greeting_audio: list[str] = []

    def speak(text: str) -> str:
        return base64.b64encode(tts.synthesize(text)).decode()

    def pseudo_for(session_id: str) -> Pseudonymizer:
        if not SESSION_RE.match(session_id):
            raise HTTPException(status_code=422, detail="session_id non valido")
        with lock:
            return pseudonymizers.setdefault(session_id, Pseudonymizer())

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(BASE_DIR / "static" / "index.html")

    @app.get("/api/greeting")
    def greeting() -> dict:
        if not greeting_audio:
            greeting_audio.append(speak(GREETING))
        return {"risposta": GREETING, "audio": greeting_audio[0]}

    @app.post("/api/turn")
    def turn(session_id: str = Form(...), audio: UploadFile = File(...)) -> dict:
        pseudo = pseudo_for(session_id)
        data = audio.file.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio troppo lungo")

        text = stt.transcribe(data) if data else ""
        if not text:
            return {"trascrizione": "", "inviato_al_server": "", "risposta": NOT_UNDERSTOOD,
                    "audio": speak(NOT_UNDERSTOOD)}

        redacted = pseudo.redact(text)
        try:
            reply = pseudo.restore(backend.chat(session_id, redacted).reply)
        except BackendError as exc:
            log.error("Errore backend: %s", exc)
            reply = "Mi dispiace, il servizio di prenotazione non è raggiungibile in questo momento. Riprova tra poco."
        return {"trascrizione": text, "inviato_al_server": redacted, "risposta": reply, "audio": speak(reply)}

    @app.post("/api/reset")
    def reset(session_id: str = Form(...)) -> dict:
        with lock:
            pseudonymizers.pop(session_id, None)
        try:
            backend.reset(session_id)
        except BackendError:
            pass
        return {"ok": True}

    @app.get("/api/health")
    def health() -> dict:
        try:
            be = backend.health()
        except BackendError as exc:
            be = {"errore": str(exc)}
        return {"voicebot": "ok", "backend": be}

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stt = SpeechToText(
        os.getenv("WHISPER_MODEL", "small"),
        os.getenv("WHISPER_DEVICE", "auto"),
        os.getenv("WHISPER_COMPUTE_TYPE", "auto"),
    )
    tts = make_tts(
        os.getenv("TTS_ENGINE", "auto"),
        BASE_DIR / os.getenv("PIPER_MODEL", "models/it_IT-paola-medium.onnx"),
    )
    backend = BackendClient(os.getenv("BACKEND_URL", "http://localhost:8000"), os.getenv("API_KEY", ""))
    app = create_app(stt, tts, backend)
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8002")))


if __name__ == "__main__":
    main()
