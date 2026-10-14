"""Conversazioni in memoria, una per session_id, con scadenza."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .agent import Conversation


@dataclass
class Session:
    conversation: Conversation = field(default_factory=Conversation)
    last_seen: float = field(default_factory=time.monotonic)
    # Serializza i turni della stessa sessione (es. doppio invio dal client).
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self, ttl_minutes: int):
        self._ttl = ttl_minutes * 60
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, s in self._sessions.items() if now - s.last_seen > self._ttl]
            for k in expired:
                del self._sessions[k]
            session = self._sessions.setdefault(session_id, Session())
            session.last_seen = now
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)
