import pytest

from stock_agents import cli
from stock_agents.compare import (
    DEFAULT_SETUPS, Comparison, Outcome, describe_setup, parse_setup, run_comparison, stock_html,
    summary_markdown, write_comparison)
from stock_agents.config import LLMConfig

BASE = LLMConfig("deepseek", "ds", "deepseek-flash", max_tokens=1000, base_url="http://gw")


@pytest.fixture(autouse=True)
def keys(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")


def test_parse_setup_assigns_models_per_agent():
    cfg = parse_setup("news=deepseek-v4-pro, outlook=anthropic:claude-sonnet-5-5", BASE)
    assert (cfg.for_agent("news").provider, cfg.for_agent("news").model) == ("deepseek", "deepseek-v4-pro")
    out = cfg.for_agent("outlook")
    assert (out.provider, out.model, out.api_key, out.base_url) == ("anthropic", "claude-sonnet-5-5", "an", None)
    assert cfg.for_agent("macro") is cfg  # not named: the .env defaults


def test_parse_setup_all_covers_every_agent():
    cfg = parse_setup("all=anthropic:claude-haiku-5-5", BASE)
    assert {cfg.for_agent(a).model for a in ("news", "macro", "fundamentals", "outlook")} == {"claude-haiku-5-5"}


@pytest.mark.parametrize("spec", ["news", "news=", "nope=deepseek:x", "news=gemini:x"])
def test_parse_setup_rejects_bad_entries(spec):
    with pytest.raises(ValueError):
        parse_setup(spec, BASE)


def test_default_setups_parse_and_differ_only_in_the_writing_agents():
    flash_sonnet, flash_pro = (parse_setup(s, BASE) for s in DEFAULT_SETUPS.values())
    for agent in ("news", "macro"):
        assert flash_sonnet.for_agent(agent).model == flash_pro.for_agent(agent).model == "deepseek-flash"
    assert flash_sonnet.for_agent("outlook").model == "claude-sonnet-5-5"
    assert flash_pro.for_agent("outlook").model == "deepseek-v4-pro"
    assert "outlook=anthropic:claude-sonnet-5-5" in describe_setup(flash_sonnet)


def _result(tag, truncated=False):
    meta = {"truncated": truncated, "input_tokens": 100, "output_tokens": 10}
    return {"news_report": f"{tag} news", "macro_report": f"{tag} macro", "fundamentals_report": f"{tag} <fund>",
            "outlook_report": f"{tag} outlook", "news_meta": meta, "macro_meta": meta,
            "fundamentals_meta": meta, "outlook_meta": {**meta, "truncated": truncated}}


def _run(setups, workflow):
    models = []
    comparison = run_comparison(["TCS", "HDFC Bank"], setups, workflow=workflow,
                                llm_builder=lambda cfg: models.append(cfg) or cfg.model)
    return comparison, models


def test_run_comparison_runs_each_stock_through_each_setup_and_builds_each_model_once():
    setups = {"a": BASE, "b": BASE.derive(model="deepseek-v4-pro")}
    comparison, models = _run(setups, lambda stock, llm: _result(f"{llm}|{stock}"))
    assert comparison.outcomes[("TCS", "b")].result["news_report"] == "deepseek-v4-pro|TCS news"
    assert len(comparison.outcomes) == 4 and len(models) == 2


def test_run_comparison_records_a_failure_and_carries_on():
    def workflow(stock, llm):
        if stock == "TCS":
            raise RuntimeError("boom")
        return _result("ok")

    comparison, _ = _run({"a": BASE}, workflow)
    assert comparison.outcomes[("TCS", "a")].error == "RuntimeError: boom"
    assert comparison.outcomes[("HDFC Bank", "a")].result["news_report"] == "ok news"


def test_outputs_show_setups_side_by_side_escape_html_and_flag_problems(tmp_path):
    comparison = Comparison(["TCS"], ["a", "b"])
    comparison.outcomes[("TCS", "a")] = Outcome(result=_result("A"), seconds=3)
    comparison.outcomes[("TCS", "b")] = Outcome(result=_result("B", truncated=True), seconds=4)
    page = stock_html("TCS", comparison)
    assert page.count("class=grid") == 4 and "A &lt;fund&gt;" in page and "<fund>" not in page
    assert "INCOMPLETE" in page
    summary = summary_markdown(comparison, {"a": "x", "b": "y"})
    assert "| TCS | a | 3s | 400 | 40 | ok |" in summary and "INCOMPLETE" in summary
    out = write_comparison(comparison, {"a": "x", "b": "y"}, tmp_path / "cmp")
    assert sorted(p.name for p in out.iterdir()) == ["summary.md", "tcs.html", "tcs.md"]
    assert "### b  (" in (out / "tcs.md").read_text()


def test_cli_compare_uses_the_default_setups_and_writes_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    seen = {}

    def fake_run(stocks, setups, notify=None):
        seen.update(stocks=stocks, setups=setups)
        c = Comparison(stocks, list(setups))
        for s in stocks:
            for label in setups:
                c.outcomes[(s, label)] = Outcome(result=_result(label))
        return c

    monkeypatch.setattr(cli, "run_comparison", fake_run)
    assert cli.main(["--db", str(tmp_path / "x.db"), "compare", "TCS", "--dir", str(tmp_path / "out")]) == 0
    assert list(seen["setups"]) == ["flash+sonnet", "flash+pro"] and seen["stocks"] == ["TCS"]
    assert len(list((tmp_path / "out").glob("*/tcs.html"))) == 1


def test_cli_compare_rejects_a_bad_setup_and_duplicate_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    db = str(tmp_path / "x.db")
    with pytest.raises(SystemExit, match="unknown agent"):
        cli.main(["--db", db, "compare", "TCS", "--setup", "m", "bogus=deepseek:x"])
    with pytest.raises(SystemExit, match="different label"):
        cli.main(["--db", db, "compare", "TCS", "--setup", "m", "news=x", "--setup", "m", "news=y"])


def test_parse_setup_temperature_alone_or_with_a_model():
    cfg = parse_setup("outlook=@0.3,news=deepseek-v4-pro@0,macro=anthropic:claude-sonnet-5-5@0.2", BASE)
    out = cfg.for_agent("outlook")
    assert (out.temperature, out.model, out.provider) == (0.3, "deepseek-flash", "deepseek")
    assert (cfg.for_agent("news").temperature, cfg.for_agent("news").model) == (0.0, "deepseek-v4-pro")
    assert cfg.for_agent("macro").temperature == 0.2
    # temperatures are shown only where they differ from the default (news's 0 equals it)
    assert describe_setup(cfg) == ("news=deepseek:deepseek-v4-pro, macro=anthropic:claude-sonnet-5-5@0.2, "
                                   "fundamentals=deepseek:deepseek-flash, outlook=deepseek:deepseek-flash@0.3")


def test_parse_setup_rejects_a_bad_temperature():
    with pytest.raises(ValueError, match="temperature"):
        parse_setup("news=deepseek:x@warm", BASE)
