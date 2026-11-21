"""Prompt management: Langfuse-served prose with a safe local fallback."""
from __future__ import annotations

import pytest

from stock_agents import observability, prompts
from stock_agents.agents import fundamentals_agent, macro_agent, news_agent, outlook_agent
from stock_agents.cli import main
from stock_agents.pillars import GENERAL


class FakeClient:
    """Stands in for the Langfuse client: `store` maps prompt name to (version, text)."""

    def __init__(self, store=None, error=None):
        self.store, self.error, self.created, self.fetches = dict(store or {}), error, [], []

    def get_prompt(self, name, *, fallback=None, **kw):
        self.fetches.append((name, {**kw, "fallback": fallback}))
        if self.error:
            raise self.error
        if name not in self.store:
            if fallback is None:
                raise type("NotFound", (Exception,), {"status_code": 404})("not found")
            return type("P", (), {"is_fallback": True, "prompt": fallback, "version": 0})()
        version, text = self.store[name]
        return type("P", (), {"is_fallback": False, "prompt": text, "version": version})()

    def create_prompt(self, *, name, prompt, labels, **kw):
        self.created.append((name, prompt, labels))
        version = self.store.get(name, (0, ""))[0] + 1
        self.store[name] = (version, prompt)
        return type("P", (), {"version": version})()

    def flush(self):
        pass


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("langfuse")
    fake = FakeClient()
    monkeypatch.setattr(observability, "enabled", lambda: True)
    import langfuse

    monkeypatch.setattr(langfuse, "get_client", lambda: fake)
    return fake


def test_the_local_prompt_is_used_when_langfuse_is_off():
    p = prompts.resolve("news", "local prose")
    assert (p.text, p.source, p.version) == ("local prose", "local", None)


def test_compose_puts_the_code_owned_block_after_the_prose():
    assert prompts.compose("prose\n\n", "BLOCK") == "prose\n\nBLOCK"
    assert news_agent.SYSTEM_PROMPT == prompts.compose(news_agent.PROSE, news_agent.structured.instructions("news"))
    assert "```json" in news_agent.SYSTEM_PROMPT and "```json" not in news_agent.PROSE


def test_a_langfuse_prompt_is_used_with_its_version(client, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "staging")
    client.store["stock-analyst/news"] = (7, "edited news prose")
    p = prompts.resolve("news", "local prose")
    assert (p.text, p.source, p.version) == ("edited news prose", "langfuse", 7)
    name, kw = client.fetches[0]
    assert name == "stock-analyst/news" and kw["label"] == "staging" and kw["fallback"] == "local prose"


def test_a_missing_prompt_or_a_failing_fetch_falls_back(client):
    assert prompts.resolve("news", "local").source == "local"  # not in Langfuse
    client.error = RuntimeError("network down")
    assert prompts.resolve("news", "local").text == "local"


def test_an_empty_prompt_falls_back(client):
    client.store["stock-analyst/news"] = (3, "   ")
    assert prompts.resolve("news", "local").source == "local"


def test_a_prompt_that_lost_a_required_heading_falls_back(client):
    broken = fundamentals_agent.SYSTEM_PROMPT.replace("## Versus Peers", "## Peers")
    client.store["stock-analyst/fundamentals"] = (2, broken)
    assert prompts.missing_headings("fundamentals", broken) == ["Versus Peers"]
    assert prompts.resolve("fundamentals", fundamentals_agent.SYSTEM_PROMPT).source == "local"
    client.store["stock-analyst/fundamentals"] = (3, fundamentals_agent.SYSTEM_PROMPT)
    assert prompts.resolve("fundamentals", "x").version == 3


def test_every_local_prompt_has_its_required_headings():
    for agent, text in prompts.defaults().items():
        assert prompts.missing_headings(agent, text) == [], agent


def test_agents_build_with_the_fetched_prose_and_keep_the_json_contract(client):
    client.store["stock-analyst/fundamentals"] = (4, fundamentals_agent.SYSTEM_PROMPT.replace("Verdict", "Verdict (edited)"))
    seen = []

    class Model:
        def invoke(self, messages):
            seen.append(messages[0].content)
            from langchain_core.messages import AIMessage
            return AIMessage("ok")

    fundamentals_agent.build_fundamentals_agent(Model(), GENERAL).invoke({"messages": []})
    assert "Verdict (edited)" in seen[0] and '"balance_sheet"' in seen[0]  # edited prose + the pillar keys from code


def test_builders_accept_an_explicit_prompt_without_fetching(client):
    macro_agent.build_macro_agent(object.__new__(type("M", (), {"bind_tools": lambda s, t: s})), prompt=prompts.Prompt("macro", "x"))
    outlook_agent.build_outlook_agent(object(), prompt=prompts.Prompt("outlook", "x"))
    assert client.fetches == []


def test_push_creates_missing_prompts_and_keeps_edited_ones(client):
    first = dict(prompts.push())
    assert all(v.startswith("created v1") for v in first.values()) and len(first) == len(prompts.PROMPTS)
    assert all(name.startswith("stock-analyst/") for name in first)
    assert all(v.startswith("unchanged") for v in dict(prompts.push()).values())
    client.store["stock-analyst/news"] = (1, "edited in the Langfuse UI")
    assert dict(prompts.push())["stock-analyst/news"].startswith("differs")
    assert client.store["stock-analyst/news"][1] == "edited in the Langfuse UI"
    assert dict(prompts.push(force=True))["stock-analyst/news"] == "new version v2"
    assert client.created[-1][2] == ["production"]


def test_push_needs_langfuse_configured():
    with pytest.raises(RuntimeError, match="not configured"):
        prompts.push()


def test_the_prompts_command(client, tmp_path, capsys):
    assert main(["prompts", "push", "--db", str(tmp_path / "t.db")]) == 0
    assert "stock-analyst/news: created v1" in capsys.readouterr().out


# -- linking generations to the prompt version, and recording it on reports ---------------------------------------

def real_prompt_client(version=3):
    from langfuse.api import Prompt_Text
    from langfuse.model import TextPromptClient

    return TextPromptClient(Prompt_Text(name="stock-analyst/news", version=version, prompt="x", labels=["production"],
                                        tags=[], config={}, type="text", commit_message=None))


def test_prompt_refs():
    assert prompts.Prompt("news", "t").ref == "news@local"
    assert prompts.Prompt("news", "t", 7, "langfuse").ref == "news@v7"


def test_linked_leaves_the_model_alone_unless_there_is_a_langfuse_prompt():
    model = object()
    assert prompts.linked(model, None) is model
    assert prompts.linked(model, prompts.Prompt("news", "t")) is model  # local default: nothing to link
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    runnable = GenericFakeChatModel(messages=iter([]))
    assert prompts.linked(runnable, prompts.Prompt("news", "t", 3, "langfuse", object())) is not runnable
    assert prompts.linked(model, prompts.Prompt("news", "t", 3, "langfuse", object())) is model  # not a Runnable


def test_generations_are_linked_to_the_prompt_version(monkeypatch):
    pytest.importorskip("langfuse")
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langfuse.langchain import CallbackHandler

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:9")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.setattr(observability, "_load_env", lambda: None)
    client_prompt = real_prompt_client()
    seen = []
    orig = CallbackHandler.on_chat_model_start

    def spy(self, *a, **k):
        seen.append(list(self._prompt_to_parent_run_map.values()))
        return orig(self, *a, **k)

    monkeypatch.setattr(CallbackHandler, "on_chat_model_start", spy)
    model = GenericFakeChatModel(messages=iter([AIMessage("hi"), AIMessage("hi again")]))
    with observability.trace("news", "TCS", 1):
        prompts.linked(model, prompts.Prompt("news", "t", 3, "langfuse", client_prompt)).invoke("hello")
        model.invoke("unlinked")
    assert seen[0] == [client_prompt]  # the call that went through `linked` has the prompt registered
    assert seen[1] == []


def test_agent_nodes_record_which_prompt_wrote_the_report(client):
    from tests.unit.test_structured import NEWS, _scripted, block

    out = news_agent.news_agent_node(_scripted("n\n\n" + block(NEWS)))({"stock": "TCS"})
    assert out["news_meta"]["prompt"] == "news@local"
    client.store["stock-analyst/news"] = (7, news_agent.PROSE)
    out = news_agent.news_agent_node(_scripted("n\n\n" + block(NEWS)))({"stock": "TCS"})
    assert out["news_meta"]["prompt"] == "news@v7"


def test_the_prompt_version_is_stored_on_the_report():
    from stock_agents.analysis import save_report
    from stock_agents.config import LLMConfig
    from stock_agents.storage import Repository, connect

    repo = Repository(connect(":memory:"))
    run_id = repo.start_run("news", "TCS")
    rid = save_report(repo, run_id, "news", "TCS", "body", {"prompt": "news@v7"}, LLMConfig("anthropic", "m", "k"))
    assert repo.get(rid).prompt_version == "news@v7"
    old = repo.save_report(run_id, "news", "body", stock="TCS")
    assert repo.get(old).prompt_version is None
