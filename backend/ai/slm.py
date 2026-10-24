"""Local SLM client (Ollama + Qwen 2.5 3B). No cloud fallback — ever."""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("slm")

AI_UNAVAILABLE = "AI service is unavailable. Please start Ollama."


class AIUnavailable(Exception):
    pass


def _client(timeout: float | None = None) -> httpx.Client:
    return httpx.Client(base_url=settings.ollama_base_url, timeout=timeout or settings.ollama_timeout)


def generate(system: str, user: str) -> str:
    """Chat completion against local Ollama, deterministic (temperature 0)."""
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": 0, "top_p": 0.9, "num_predict": 512, "seed": 7},
        "keep_alive": "30m",
    }
    try:
        with _client() as c:
            r = c.post("/api/chat", json=payload)
        if r.status_code == 404:
            log.error("Ollama model %s not found", settings.ollama_model)
            raise AIUnavailable(f"AI model '{settings.ollama_model}' is not installed. "
                                f"Run: ollama pull {settings.ollama_model}")
        r.raise_for_status()
        return (r.json().get("message") or {}).get("content", "").strip()
    except AIUnavailable:
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
        log.warning("Ollama unreachable: %s", exc)
        raise AIUnavailable(AI_UNAVAILABLE) from exc
    except httpx.HTTPError as exc:
        log.exception("Ollama error")
        raise AIUnavailable(AI_UNAVAILABLE) from exc


def status() -> dict:
    """Health of Ollama and whether the configured model is installed."""
    try:
        with _client(3) as c:
            r = c.get("/api/tags")
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        want = settings.ollama_model
        installed = want in names or f"{want}:latest" in names
        return {"online": True, "model": want, "model_installed": installed}
    except Exception:  # noqa: BLE001
        return {"online": False, "model": settings.ollama_model, "model_installed": False}
