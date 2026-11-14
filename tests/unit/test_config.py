import pytest

from stock_agents.config import LLMConfig

VARS = [
    "LLM_PROVIDER", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "LLM_MODEL", "LLM_TEMPERATURE", "LLM_MAX_TOKENS", "LLM_BASE_URL", "STOCK_ANALYST_DB",
    "STOCK_ANALYST_NEWS_MAX_AGE_HOURS", "STOCK_ANALYST_MACRO_MAX_AGE_HOURS",
    "STOCK_ANALYST_FUNDAMENTALS_MAX_AGE_HOURS",
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
        "anthropic", "claude-sonnet-5-5", 0.0, 8192, None)


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


def test_warns_when_shell_variable_overrides_dotenv(tmp_path, monkeypatch, caplog):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=k\nLLM_MAX_TOKENS=16000\n")
    monkeypatch.setenv("LLM_MAX_TOKENS", "2048")
    with caplog.at_level("INFO"):
        cfg = LLMConfig.from_env()
    assert cfg.max_tokens == 2048  # precedence unchanged
    assert "LLM_MAX_TOKENS=2048 comes from your shell environment" in caplog.text
    assert "unset LLM_MAX_TOKENS" in caplog.text and str(tmp_path) in caplog.text


def test_no_warning_when_values_agree(tmp_path, monkeypatch, caplog):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=k\nLLM_MAX_TOKENS=16000\n")
    monkeypatch.setenv("LLM_MAX_TOKENS", "16000")
    with caplog.at_level("WARNING"):
        LLMConfig.from_env()
    assert caplog.text == ""


def test_api_key_override_warning_does_not_leak_values(tmp_path, monkeypatch, caplog):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=file-secret\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shell-secret")
    with caplog.at_level("WARNING"):
        LLMConfig.from_env()
    assert "ANTHROPIC_API_KEY from your shell" in caplog.text
    assert "file-secret" not in caplog.text and "shell-secret" not in caplog.text


def test_db_path_defaults_next_to_the_dotenv_file(tmp_path, monkeypatch):
    from stock_agents.config import db_path

    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / ".env").write_text("X=1\n")
    monkeypatch.chdir(project / "sub")  # running from a subfolder must still find the project's database
    assert db_path() == project / "data" / "stock_analyst.db"


def test_db_path_without_dotenv_is_relative_to_cwd(tmp_path):
    from stock_agents.config import db_path

    assert db_path() == tmp_path / "data" / "stock_analyst.db"


def test_db_path_from_env_relative_and_absolute(tmp_path, monkeypatch):
    from stock_agents.config import db_path

    (tmp_path / ".env").write_text("STOCK_ANALYST_DB=mine/x.db\n")
    assert db_path() == tmp_path / "mine" / "x.db"
    monkeypatch.setenv("STOCK_ANALYST_DB", str(tmp_path / "abs" / "y.db"))
    assert db_path() == tmp_path / "abs" / "y.db"


def test_reuse_hours_default_env_and_dotenv(tmp_path, monkeypatch):
    from stock_agents.config import reuse_hours

    assert reuse_hours("news") == reuse_hours("macro") == 6.0
    (tmp_path / ".env").write_text("STOCK_ANALYST_NEWS_MAX_AGE_HOURS=2.5\n")
    assert reuse_hours("news") == 2.5 and reuse_hours("macro") == 6.0
    monkeypatch.setenv("STOCK_ANALYST_MACRO_MAX_AGE_HOURS", "0")
    assert reuse_hours("macro") == 0.0


@pytest.mark.parametrize("bad", ["soon", "-2"])
def test_reuse_hours_rejects_bad_values(monkeypatch, bad):
    from stock_agents.config import reuse_hours

    monkeypatch.setenv("STOCK_ANALYST_NEWS_MAX_AGE_HOURS", bad)
    with pytest.raises(RuntimeError, match="STOCK_ANALYST_NEWS_MAX_AGE_HOURS"):
        reuse_hours("news")


def test_fundamentals_reuse_defaults_to_a_week_and_is_configurable(monkeypatch):
    from stock_agents.config import reuse_hours

    assert reuse_hours("fundamentals") == 168.0 and reuse_hours("news") == 6.0
    monkeypatch.setenv("STOCK_ANALYST_FUNDAMENTALS_MAX_AGE_HOURS", "24")
    assert reuse_hours("fundamentals") == 24.0


AGENT_VARS = [f"LLM_{a}_{k}" for a in ("NEWS", "MACRO", "FUNDAMENTALS", "OUTLOOK")
              for k in ("PROVIDER", "MODEL", "TEMPERATURE", "MAX_TOKENS", "BASE_URL")]


@pytest.fixture
def agent_env(monkeypatch):
    for v in AGENT_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")
    monkeypatch.setenv("LLM_BASE_URL", "http://ds-gw")
    monkeypatch.setenv("LLM_MAX_TOKENS", "1000")


def test_no_agent_settings_means_no_overrides(agent_env):
    cfg = LLMConfig.from_env()
    assert cfg.agent_overrides == () and cfg.describe() == ("deepseek", "deepseek-flash")
    assert cfg.for_agent("outlook") is cfg


def test_agent_model_on_the_same_provider_inherits_everything_else(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_OUTLOOK_MODEL", "deepseek-v4-pro")
    cfg = LLMConfig.from_env()
    out = cfg.for_agent("outlook")
    assert (out.provider, out.api_key, out.model, out.max_tokens, out.base_url) == (
        "deepseek", "ds", "deepseek-v4-pro", 1000, "http://ds-gw")
    assert cfg.for_agent("news") is cfg and [a for a, _ in cfg.agent_overrides] == ["outlook"]


def test_agent_on_another_provider_gets_its_key_default_model_and_no_inherited_base_url(agent_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an")
    monkeypatch.setenv("LLM_FUNDAMENTALS_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_FUNDAMENTALS_MAX_TOKENS", "20000")
    out = LLMConfig.from_env().for_agent("fundamentals")
    assert (out.provider, out.api_key, out.model, out.max_tokens, out.base_url) == (
        "anthropic", "an", "claude-sonnet-5-5", 20000, None)


def test_agent_on_another_provider_needs_that_providers_key(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_OUTLOOK_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        LLMConfig.from_env()


def test_unsupported_agent_provider_raises(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_PROVIDER", "nope")
    with pytest.raises(RuntimeError, match="Unsupported"):
        LLMConfig.from_env()


def test_override_equal_to_the_base_is_not_an_override(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_MODEL", "deepseek-flash")
    assert LLMConfig.from_env().agent_overrides == ()


def test_describe_lists_every_agent_when_they_differ(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_OUTLOOK_MODEL", "deepseek-v4-pro")
    provider, model = LLMConfig.from_env().describe()
    assert provider == "mixed"
    assert model == ("news=deepseek:deepseek-flash, macro=deepseek:deepseek-flash, "
                     "fundamentals=deepseek:deepseek-flash, outlook=deepseek:deepseek-v4-pro")


def test_agent_temperature_overrides_the_global_one_and_other_settings_are_inherited(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.1")
    monkeypatch.setenv("LLM_OUTLOOK_TEMPERATURE", "0.3")
    cfg = LLMConfig.from_env()
    out = cfg.for_agent("outlook")
    assert (out.temperature, out.model, out.max_tokens, out.base_url) == (0.3, "deepseek-flash", 1000, "http://ds-gw")
    assert cfg.for_agent("news").temperature == 0.1
    assert cfg.describe()[1].endswith("outlook=deepseek:deepseek-flash@0.3")
    assert "news=deepseek:deepseek-flash," in cfg.describe()[1]


def test_agent_temperature_zero_is_an_override_not_unset(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.5")
    monkeypatch.setenv("LLM_NEWS_TEMPERATURE", "0")
    assert LLMConfig.from_env().for_agent("news").temperature == 0.0


def test_agent_temperature_must_be_a_number(agent_env, monkeypatch):
    monkeypatch.setenv("LLM_NEWS_TEMPERATURE", "warm")
    with pytest.raises(RuntimeError, match="LLM_NEWS_TEMPERATURE"):
        LLMConfig.from_env()
