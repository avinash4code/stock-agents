"""LLM configuration, read from the environment (a `.env` file is loaded if present)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

# provider -> (API key env var, default model)
PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-5-5"),
    "deepseek": ("DEEPSEEK_API_KEY", "deepseek-chat"),
}
DEFAULT_PROVIDER = "anthropic"


DEFAULT_DB = "data/stock_analyst.db"
_reported: set[tuple[str, str, str]] = set()  # (env file, key, shell value) already warned about


def _load_env_file() -> None:
    """Load `.env` (searched upward from the current directory); real env vars win over it.

    Because that precedence silently hides `.env` edits, say where the file is and warn
    about every setting a shell variable overrides.
    """
    path = find_dotenv(usecwd=True)
    if not path:
        logger.info("No .env file found; using environment variables and defaults")
        return
    logger.info("Loading settings from %s", path)
    for key, file_value in dotenv_values(path).items():
        shell_value = os.environ.get(key)
        if shell_value is not None and file_value is not None and shell_value != file_value:
            if (path, key, shell_value) in _reported:
                continue
            _reported.add((path, key, shell_value))
            if key.endswith("API_KEY"):
                logger.warning("%s from your shell environment overrides the one in %s", key, path)
            else:
                logger.warning(
                    "%s=%s comes from your shell environment and overrides %s=%s in %s "
                    "(run `unset %s` to use the .env value)",
                    key, shell_value, key, file_value, path, key,
                )
    load_dotenv(path, override=False)


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 8192
    base_url: str | None = None

    @classmethod
    def from_env(cls, load_env_file: bool = True) -> "LLMConfig":
        if load_env_file:
            _load_env_file()
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
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
            base_url=(os.getenv("LLM_BASE_URL") or "").strip() or None,
        )


def db_path(load_env_file: bool = True) -> Path:
    """Database location: `STOCK_ANALYST_DB`, else `data/stock_analyst.db`.

    A relative path is resolved against the folder holding `.env` (so the same database is
    used wherever you run the command from), or the current directory if there is no `.env`.
    """
    env_file = ""
    if load_env_file:
        env_file = find_dotenv(usecwd=True)
        _load_env_file()
    configured = Path(os.getenv("STOCK_ANALYST_DB", "").strip() or DEFAULT_DB).expanduser()
    if configured.is_absolute():
        return configured
    return (Path(env_file).parent if env_file else Path.cwd()) / configured


DEFAULT_MAX_AGE_HOURS = 6.0
# Quarterly results change about four times a year, so a fundamentals report is reused for a week.
DEFAULT_MAX_AGE_BY_KIND = {"fundamentals": 24.0 * 7}


def reuse_hours(kind: str, load_env_file: bool = True) -> float:
    """How many hours a stored `news`, `macro` or `fundamentals` report is reused instead of re-running
    its agent: `STOCK_ANALYST_NEWS_MAX_AGE_HOURS` / `..._MACRO_...` (default 6) or
    `STOCK_ANALYST_FUNDAMENTALS_MAX_AGE_HOURS` (default 168, one week). 0 turns reuse off."""
    if load_env_file:
        _load_env_file()
    var = f"STOCK_ANALYST_{kind.upper()}_MAX_AGE_HOURS"
    raw = os.getenv(var, "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_BY_KIND.get(kind, DEFAULT_MAX_AGE_HOURS)
    try:
        hours = float(raw)
    except ValueError:
        raise RuntimeError(f"{var} must be a number of hours, got {raw!r}") from None
    if hours < 0:
        raise RuntimeError(f"{var} must be >= 0, got {raw!r}")
    return hours
