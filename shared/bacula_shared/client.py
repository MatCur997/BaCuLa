"""Client HTTP verso il backend, usato da CHAT e VOICE."""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx


class BackendError(Exception):
    """Errore di comunicazione col backend, con messaggio mostrabile all'utente."""


@dataclass
class ChatReply:
    reply: str
    tools_used: list[str] = field(default_factory=list)


class BackendClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    def chat(self, session_id: str, message: str) -> ChatReply:
        """Invia un messaggio GIÀ pseudonimizzato e restituisce la risposta dell'assistente."""
        data = self._request("POST", "/api/chat", json={"session_id": session_id, "message": message})
        return ChatReply(reply=data["reply"], tools_used=data.get("tools_used", []))

    def reset(self, session_id: str) -> None:
        self._request("DELETE", f"/api/sessions/{session_id}")

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise BackendError("Il server sta impiegando troppo tempo a rispondere.") from exc
        except httpx.HTTPError as exc:
            raise BackendError("Server di prenotazione non raggiungibile.") from exc
        if resp.status_code == 401:
            raise BackendError("Chiave API non valida: controlla API_KEY nel file .env.")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise BackendError(f"Errore dal server ({resp.status_code}): {detail}")
        return resp.json() if resp.content else {}

    def close(self) -> None:
        self._http.close()
