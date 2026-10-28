import pytest

from stock_agents.config import LLMConfig

VARS = [
    "LLM_PROVIDER", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "LLM_MODEL", "LLM_TEMPERATURE", "LLM_MAX_TOKENS", "LLM_BASE_URL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env from the repo


def test_defaults_to_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = LLMConfig.from_env()
    assert (cfg.provider, cfg.model, cfg.temperature, cfg.max_tokens, cfg.base_url) == (
        "anthropic", "claude-sonnet-5-5", 0.0, 2048, None)


def test_deepseek_uses_its_key_and_default_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "DeepSeek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "other")
    cfg = LLMConfig.from_env()
    assert (cfg.provider, cfg.api_key, cfg.model) == ("deepseek", "ds", "deepseek-chat")


def test_missing_key_names_the_provider_var(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")  # wrong provider's key must not satisfy it
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        LLMConfig.from_env()


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(RuntimeError, match="Unsupported"):
        LLMConfig.from_env()


def test_reads_dotenv_file(tmp_path):
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=from-file\nLLM_MODEL=m1\n"
        "LLM_TEMPERATURE=0.5\nLLM_MAX_TOKENS=100\nLLM_BASE_URL=http://gw\n"
    )
    cfg = LLMConfig.from_env()
    assert cfg == LLMConfig("deepseek", "from-file", "m1", 0.5, 100, "http://gw")


def test_real_env_beats_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert LLMConfig.from_env().api_key == "from-env"
