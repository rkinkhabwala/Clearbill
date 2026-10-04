"""Runtime settings, overridable via environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    vision_model: str = field(default_factory=lambda: os.getenv("CLEARBILL_VISION_MODEL", "gemma4"))
    text_model: str = field(default_factory=lambda: os.getenv("CLEARBILL_TEXT_MODEL", "gemma4"))
    num_ctx: int = field(default_factory=lambda: int(os.getenv("CLEARBILL_NUM_CTX", "8192")))
    keep_alive: str = field(default_factory=lambda: os.getenv("CLEARBILL_KEEP_ALIVE", "10m"))
    render_dpi: int = 150
    max_image_px: int = 1600
    tolerance: float = 0.01  # dollars


settings = Settings()
