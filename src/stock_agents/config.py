"""LLM configuration, read from the environment (a `.env` file is loaded if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

# provider -> (API key env var, default model)
PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-5-5"),
    "deepseek": ("DEEPSEEK_API_KEY", "deepseek-chat"),
}
DEFAULT_PROVIDER = "anthropic"


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 2048
    base_url: str | None = None

    @classmethod
    def from_env(cls, load_env_file: bool = True) -> "LLMConfig":
        if load_env_file:
            # Real environment variables win over .env values.
            load_dotenv(find_dotenv(usecwd=True), override=False)
        provider = (os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
        if provider not in PROVIDERS:
            raise RuntimeError(
                f"Unsupported LLM_PROVIDER {provider!r}; choose one of {sorted(PROVIDERS)}."
            )
        key_var, default_model = PROVIDERS[provider]
        api_key = os.getenv(key_var, "").strip()
        if not api_key:
            raise RuntimeError(f"{key_var} is not set. Copy .env.example to .env and fill it in.")
        return cls(
            provider=provider,
            api_key=api_key,
            model=(os.getenv("LLM_MODEL") or "").strip() or default_model,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
            base_url=(os.getenv("LLM_BASE_URL") or "").strip() or None,
        )
