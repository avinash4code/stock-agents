"""LLM configuration, read from the environment (a `.env` file is loaded if present)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

# provider -> (API key env var, default model)
PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-5-5"),
    "deepseek": ("DEEPSEEK_API_KEY", "deepseek-chat"),
}
DEFAULT_PROVIDER = "anthropic"
# The agents that can each use their own model (see `LLMConfig.for_agent`).
AGENTS = ("news", "macro", "fundamentals", "outlook")


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


def _provider_key(provider: str) -> str:
    key_var, _ = PROVIDERS[provider]
    api_key = os.getenv(key_var, "").strip()
    if not api_key:
        raise RuntimeError(f"{key_var} is not set. Copy .env.example to .env and fill it in.")
    return api_key


def _number(var: str, raw: str, kind):
    """`raw` (an env var's text) as a number, or None if empty."""
    if not raw:
        return None
    try:
        return kind(raw)
    except ValueError:
        raise RuntimeError(f"{var} must be a number, got {raw!r}") from None


def _provider(name: str, source: str) -> str:
    provider = name.strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"Unsupported {source} {provider!r}; choose one of {sorted(PROVIDERS)}.")
    return provider


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 8192
    base_url: str | None = None
    # Agents that use another model than the rest: (agent name, config) pairs, see `with_agent_models`.
    agent_overrides: tuple[tuple[str, "LLMConfig"], ...] = field(default=(), compare=True)

    @classmethod
    def from_env(cls, load_env_file: bool = True) -> "LLMConfig":
        """Settings from `LLM_*`. `LLM_<AGENT>_PROVIDER|MODEL|TEMPERATURE|MAX_TOKENS|BASE_URL` (agent = NEWS, MACRO,
        FUNDAMENTALS or OUTLOOK) give one agent its own model settings; whatever is not set there is inherited."""
        if load_env_file:
            _load_env_file()
        provider = _provider(os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER, "LLM_PROVIDER")
        _, default_model = PROVIDERS[provider]
        base = cls(
            provider=provider,
            api_key=_provider_key(provider),
            model=(os.getenv("LLM_MODEL") or "").strip() or default_model,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
            base_url=(os.getenv("LLM_BASE_URL") or "").strip() or None,
        )
        overrides = {}
        for agent in AGENTS:
            prefix = f"LLM_{agent.upper()}_"
            values = {k: (os.getenv(prefix + k) or "").strip() for k in ("PROVIDER", "MODEL", "TEMPERATURE", "MAX_TOKENS", "BASE_URL")}
            if any(values.values()):
                overrides[agent] = base.derive(
                    provider=values["PROVIDER"] or None, model=values["MODEL"] or None,
                    temperature=_number(prefix + "TEMPERATURE", values["TEMPERATURE"], float),
                    max_tokens=_number(prefix + "MAX_TOKENS", values["MAX_TOKENS"], int),
                    base_url=values["BASE_URL"] or None, source=prefix + "PROVIDER",
                )
        return base.with_agent_models(overrides)

    def derive(self, *, provider: str | None = None, model: str | None = None, temperature: float | None = None,
               max_tokens: int | None = None, base_url: str | None = None, source: str = "provider") -> "LLMConfig":
        """A config for one agent: the given settings, the rest inherited. Another provider means its own API key,
        its default model (unless one is given) and no inherited `base_url`, which belongs to this provider."""
        provider = _provider(provider, source) if provider else self.provider
        switched = provider != self.provider
        return LLMConfig(
            provider=provider,
            api_key=_provider_key(provider) if switched else self.api_key,
            model=model or (PROVIDERS[provider][1] if switched else self.model),
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self.max_tokens,
            base_url=base_url or (None if switched else self.base_url),
        )

    def with_agent_models(self, overrides: dict[str, "LLMConfig"]) -> "LLMConfig":
        unknown = set(overrides) - set(AGENTS)
        if unknown:
            raise RuntimeError(f"Unknown agent(s) {sorted(unknown)}; choose from {list(AGENTS)}.")
        pairs = tuple((a, overrides[a]) for a in AGENTS if a in overrides and overrides[a] != self.for_agent(a))
        return LLMConfig(self.provider, self.api_key, self.model, self.temperature, self.max_tokens,
                         self.base_url, pairs)

    def for_agent(self, agent: str) -> "LLMConfig":
        return dict(self.agent_overrides).get(agent, self)

    def describe(self) -> tuple[str, str]:
        """(provider, model) as recorded with a run; `mixed` plus a per-agent list when agents differ."""
        if not self.agent_overrides:
            return self.provider, self.model
        return "mixed", ", ".join(f"{a}={self.describe_agent(a)}" for a in AGENTS)

    def describe_agent(self, agent: str) -> str:
        """`provider:model`, plus `@temperature` where this agent's differs from the default one."""
        cfg = self.for_agent(agent)
        temp = f"@{cfg.temperature:g}" if cfg.temperature != self.temperature else ""
        return f"{cfg.provider}:{cfg.model}{temp}"


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
