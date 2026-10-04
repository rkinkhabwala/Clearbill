"""Thin wrapper around the local Ollama server.

`structured()` uses Ollama's JSON-schema constrained decoding (`format=`) and validates the
result with Pydantic, retrying on validation failure. Nothing here talks to anything but the
configured Ollama host.
"""
from __future__ import annotations

import logging
import time
from typing import TypeVar

from ollama import Client
from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = Client(host=settings.ollama_host)
    return _client


def _options(temperature: float) -> dict:
    return {"temperature": temperature, "num_ctx": settings.num_ctx}


def structured(
    model: str,
    prompt: str,
    schema: type[T],
    *,
    system: str | None = None,
    images: list[bytes] | None = None,
    retries: int = 2,
) -> T:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    user = {"role": "user", "content": prompt}
    if images:
        user["images"] = images
    messages.append(user)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        start = time.perf_counter()
        resp = client().chat(
            model=model,
            messages=messages,
            format=schema.model_json_schema(),
            options=_options(0.0),
            keep_alive=settings.keep_alive,
        )
        log.info("structured %s attempt=%d %.1fs", schema.__name__, attempt, time.perf_counter() - start)
        try:
            return schema.model_validate_json(resp.message.content)
        except ValidationError as e:  # constrained decoding makes this rare, but be safe
            last_error = e
    raise RuntimeError(f"{model} did not return a valid {schema.__name__}: {last_error}")


def text(model: str, prompt: str, *, system: str | None = None, temperature: float = 0.3) -> str:
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    resp = client().chat(model=model, messages=messages, options=_options(temperature), keep_alive=settings.keep_alive)
    return resp.message.content.strip()


def available_models() -> set[str]:
    resp = client().list()
    names: set[str] = set()
    for m in resp.models:
        name = m.model
        names.add(name)
        names.add(name.split(":")[0])
    return names
