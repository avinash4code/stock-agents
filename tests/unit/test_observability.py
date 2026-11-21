"""Langfuse tracing: off by default, and wired into runs and scores when keys are set."""
from __future__ import annotations

import json
import os

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import var_child_runnable_config

from stock_agents import observability, structured
from stock_agents.analysis import save_report, tracked_run
from stock_agents.config import LLMConfig
from stock_agents.storage import Repository, connect
from tests.unit.test_structured import OUTLOOK, Repairer, block

KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test", "LANGFUSE_HOST": "http://127.0.0.1:9"}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def langfuse_on(monkeypatch):
    pytest.importorskip("langfuse")
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")  # real SDK objects, but nothing is exported
    monkeypatch.setattr(observability, "_load_env", lambda: None)


@pytest.fixture
def langfuse_off(monkeypatch):
    monkeypatch.setattr(observability, "_load_env", lambda: None)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


class Recorder:
    def __init__(self):
        self.scores = {}

    def create_score(self, *, name, value, **kw):
        self.scores[name] = value


def with_recorder():
    rec = Recorder()
    tr = observability.Trace(rec, None, "t" * 32)
    return rec, tr, observability._current.set(tr)


def test_tracing_is_off_without_keys(langfuse_off):
    assert not observability.enabled() and observability.trace_url("abc") is None
    with observability.trace("all", "TCS", 1) as tr:
        assert tr is None and observability.current() is None
    observability.score_report("news", {"structured": None})  # no-op, must not raise
    observability.flush()


def test_it_can_be_switched_off_with_the_keys_present(langfuse_on, monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    assert not observability.enabled()


def test_a_trace_makes_its_handler_the_ambient_callback(langfuse_on):
    from langfuse.langchain import CallbackHandler

    with observability.trace("all", "TCS", 7, {"news": "deepseek/x"}) as tr:
        assert tr is not None and len(tr.trace_id) == 32 and observability.current() is tr
        callbacks = (var_child_runnable_config.get() or {}).get("callbacks")
        assert any(isinstance(c, CallbackHandler) for c in callbacks)
    assert observability.current() is None and var_child_runnable_config.get() is None


def test_runs_inside_a_trace_reach_the_handler(langfuse_on):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langfuse.langchain import CallbackHandler

    seen = []
    orig = CallbackHandler.on_chat_model_start

    def spy(self, *a, **k):
        seen.append(k.get("tags"))
        return orig(self, *a, **k)

    CallbackHandler.on_chat_model_start = spy
    try:
        with observability.trace("news", "TCS", 1):
            GenericFakeChatModel(messages=iter([AIMessage("hi")])).invoke("hello")  # no config passed
    finally:
        CallbackHandler.on_chat_model_start = orig
    assert seen, "an LLM call made inside trace() did not reach the Langfuse handler"


def test_trace_url_uses_the_configured_host(langfuse_on):
    assert observability.trace_url("abc") == "http://127.0.0.1:9/trace/abc"
    assert observability.trace_url(None) is None


def test_a_setup_failure_never_breaks_the_run(langfuse_on, monkeypatch):
    import langfuse

    monkeypatch.setattr(langfuse, "get_client", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with observability.trace("all", "TCS", 1) as tr:
        assert tr is None


def test_tracked_run_stores_the_trace_id_and_still_records_failures(langfuse_on, conn):
    repo, cfg = Repository(conn), LLMConfig("anthropic", "m", "k")
    with tracked_run(conn, "all", "TCS", cfg) as run_id:
        assert observability.current() is not None
    run = repo.get_run(run_id)
    assert run.status == "ok" and run.trace_id and len(run.trace_id) == 32
    with pytest.raises(ValueError):
        with tracked_run(conn, "all", "TCS", cfg) as failed_id:
            raise ValueError("bad")
    assert repo.get_run(failed_id).status == "failed"


def test_tracked_run_has_no_trace_id_when_tracing_is_off(langfuse_off, conn):
    with tracked_run(conn, "all", "TCS", LLMConfig("anthropic", "m", "k")) as run_id:
        pass
    assert Repository(conn).get_run(run_id).trace_id is None


def test_report_scores():
    rec, _, token = with_recorder()
    try:
        meta = {"structured": {"x": 1}, "repaired": True, "truncated": False, "input_tokens": 10, "output_tokens": 4}
        observability.score_report("news", meta)
        observability.score_report("macro", None, reused=True)
        observability.score_unavailable("fundamentals", "no data")
    finally:
        observability._current.reset(token)
    assert rec.scores == {
        "news.reused": 0.0, "news.structured_valid": 1.0, "news.structured_repaired": 1.0, "news.truncated": 0.0,
        "news.input_tokens": 10.0, "news.output_tokens": 4.0, "macro.reused": 1.0, "fundamentals.unavailable": 1.0,
    }


def test_a_missing_block_scores_invalid():
    rec, _, token = with_recorder()
    try:
        observability.score_report("outlook", {"structured": None, "truncated": True})
    finally:
        observability._current.reset(token)
    assert rec.scores["outlook.structured_valid"] == 0.0 and rec.scores["outlook.truncated"] == 1.0


def test_save_report_scores_the_report(conn):
    repo, cfg = Repository(conn), LLMConfig("anthropic", "m", "k")
    rec, _, token = with_recorder()
    try:
        with conn:
            run_id = repo.start_run("news", "TCS")
            save_report(repo, run_id, "news", "TCS", "text", {"structured": None, "repaired": False}, cfg)
    finally:
        observability._current.reset(token)
    assert rec.scores["news.structured_valid"] == 0.0


def test_extract_detailed_says_whether_the_repair_call_was_needed():
    _, data, repaired = structured.extract_detailed("outlook", f"x\n\n{block(OUTLOOK)}")
    assert data and not repaired
    _, data, repaired = structured.extract_detailed("outlook", "x", repair_llm=Repairer(json.dumps(OUTLOOK)))
    assert data and repaired
    _, data, repaired = structured.extract_detailed("outlook", "x")
    assert data is None and not repaired
