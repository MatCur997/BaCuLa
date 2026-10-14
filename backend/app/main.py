"""API HTTP del backend. Avvio: python -m app (vedi __main__.py)."""
from __future__ import annotations

import logging
import secrets
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent, LLMClient, LLMError, OllamaClient
from .config import Settings
from .db import Database
from .restaurant import Restaurant
from .services import BookingError, BookingService
from .sessions import SessionStore

log = logging.getLogger("bacula")
STATIC_DIR = Path(__file__).parent / "static"
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN, description="Identificativo anonimo della conversazione")
    message: str = Field(min_length=1, max_length=2000, description="Testo già privato dei dati personali")


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str]


def create_app(settings: Settings, llm: LLMClient | None = None) -> FastAPI:
    restaurant = Restaurant.load(settings.restaurant_path)
    db = Database(settings.db_path)
    db.init(restaurant.tables)
    service = BookingService(db, restaurant)
    llm = llm or OllamaClient(settings.ollama_url, settings.ollama_model, settings.llm_temperature,
                              settings.ollama_think, settings.llm_timeout)
    agent = Agent(llm, service, restaurant)
    sessions = SessionStore(settings.session_ttl_minutes)

    app = FastAPI(title=f"{restaurant.name} - Backend prenotazioni", version="1.0.0")

    def require_api_key(x_api_key: str = Header(default="")) -> None:
        if not secrets.compare_digest(x_api_key.encode(), settings.api_key.encode()):
            raise HTTPException(status_code=401, detail="API key non valida")

    @app.get("/api/health")
    def health() -> dict:
        available = llm.is_available() if isinstance(llm, OllamaClient) else True
        return {
            "status": "ok",
            "ristorante": restaurant.name,
            "modello": getattr(llm, "model", "n/d"),
            "modello_disponibile": available,
            "sessioni_attive": len(sessions),
        }

    @app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
    def chat(req: ChatRequest) -> ChatResponse:
        session = sessions.get(req.session_id)
        with session.lock:
            try:
                result = agent.reply(session.conversation, req.message)
            except LLMError as exc:
                log.error("LLM non disponibile: %s", exc)
                raise HTTPException(status_code=503, detail="Il modello linguistico non è disponibile.") from exc
        return ChatResponse(reply=result.reply, tools_used=result.tools_used)

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_api_key)])
    def reset_session(session_id: str) -> dict:
        sessions.delete(session_id)
        return {"ok": True}

    @app.get("/api/admin/overview", dependencies=[Depends(require_api_key)])
    def overview(day: str = Query(default_factory=lambda: date.today().isoformat(), alias="date")) -> dict:
        try:
            return service.overview(day)
        except BookingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/dashboard", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "dashboard.html")

    return app


def get_app() -> FastAPI:
    """Factory per uvicorn: legge la configurazione dal .env solo all'avvio del server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(Settings.from_env())
