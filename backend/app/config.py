"""Configurazione letta da variabili d'ambiente / file .env (mai versionato)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "sì")


@dataclass(frozen=True)
class Settings:
    api_key: str
    host: str = "0.0.0.0"
    port: int = 8000
    db_path: Path = BASE_DIR / "data" / "bacula.db"
    restaurant_path: Path = BASE_DIR / "data" / "restaurant.json"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:latest"
    ollama_think: bool = True
    llm_temperature: float = 0.2
    llm_timeout: float = 120.0
    session_ttl_minutes: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("API_KEY mancante: copia .env.example in .env e imposta una chiave.")
        return cls(
            api_key=api_key,
            host=os.getenv("HOST", cls.host),
            port=int(os.getenv("PORT", cls.port)),
            db_path=Path(os.getenv("DB_PATH", cls.db_path)),
            restaurant_path=Path(os.getenv("RESTAURANT_PATH", cls.restaurant_path)),
            ollama_url=os.getenv("OLLAMA_URL", cls.ollama_url),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            ollama_think=_bool("OLLAMA_THINK", cls.ollama_think),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", cls.llm_temperature)),
            llm_timeout=float(os.getenv("LLM_TIMEOUT", cls.llm_timeout)),
            session_ttl_minutes=int(os.getenv("SESSION_TTL_MINUTES", cls.session_ttl_minutes)),
        )
