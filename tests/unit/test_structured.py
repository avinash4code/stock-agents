import json

import pytest
from langchain_core.messages import AIMessage

from stock_agents import structured
from stock_agents.agents.common import run_agent
from stock_agents.agents.outlook_agent import build_outlook_agent
from stock_agents.pillars import FINANCIAL, GENERAL
from tests.conftest import FakeToolCallingModel

H = {"stance": "neutral", "confidence": "medium"}
OUTLOOK = {"version": 1, "headline": "Fast growth, thin margins.",
           "company": {"short": H, "medium": H, "long": {"stance": "positive", "confidence": "low"}},
           "sector": {"short": H, "medium": H, "long": H}}


def pillars(profile, **ratings):
    return [{"key": k, "rating": ratings.get(k, 3), "evidence": f"{k} evidence"} for k in profile.keys]


def fundamentals(profile=GENERAL, **extra):
    return {"version": 1, "stance": "neutral", "confidence": "medium", "headline": "Growth without cash.",
            "pillars": pillars(profile), "red_flags": ["a", "b", "c", "d"], "strengths": ["s"],
            "watch_next_quarter": ["margin above 12%"], **extra}


def block(data) -> str:
    return f"```json\n{json.dumps(data)}\n```"


def test_split_block_removes_the_last_fenced_block_only():
    text = f"## Report\n\n```json\n{{\"example\": 1}}\n```\n\nmore\n\n{block(OUTLOOK)}"
    body, raw = structured.split_block(text)
    assert json.loads(raw) == OUTLOOK
    assert body.endswith("more") and '"example"' in body and "headline" not in body


def test_split_block_without_a_block_leaves_the_text():
    assert structured.split_block("just prose\n") == ("just prose", None)


def test_unclosed_block_from_a_cut_off_reply_is_not_a_block():
    assert structured.split_block('text\n```json\n{"a": 1')[1] is None


def test_outlook_block_is_validated_and_split_off():
    body, data = structured.extract("outlook", f"## Sector\nx\n\n{block(OUTLOOK)}")
    assert body == "## Sector\nx" and data["company"]["long"]["stance"] == "positive"


def test_fundamentals_flags_are_trimmed_to_three_and_labels_come_from_the_profile():
    body, data = structured.extract("fundamentals", "r\n" + block(fundamentals()), profile=GENERAL)
    assert data["red_flags"] == ["a", "b", "c"]
    assert data["profile"] == "general"
    assert [p["label"] for p in data["pillars"]][:2] == ["Growth", "Profitability"]


def test_pillars_are_put_in_profile_order():
    data = fundamentals()
    data["pillars"].reverse()
    _, out = structured.extract("fundamentals", block(data), profile=GENERAL)
    assert [p["key"] for p in out["pillars"]] == GENERAL.keys


@pytest.mark.parametrize("mutate", [
    lambda d: d["pillars"].pop(),                                     # a pillar is missing
    lambda d: d["pillars"].append({"key": "extra", "rating": 3}),     # an extra one
    lambda d: d["pillars"][0].update(rating=6),                       # out of range
    lambda d: d.update(stance="bullish"),                             # not an allowed stance
    lambda d: d.update(surprise=1),                                   # unknown key
])
def test_invalid_blocks_give_no_structure(mutate):
    data = fundamentals()
    mutate(data)
    body, out = structured.extract("fundamentals", "report\n" + block(data), profile=GENERAL)
    assert out is None and body == "report"  # the block is still stripped from the report


def test_the_wrong_profile_is_rejected():
    _, out = structured.extract("fundamentals", block(fundamentals(GENERAL)), profile=FINANCIAL)
    assert out is None


def test_null_rating_is_allowed():
    data = fundamentals(FINANCIAL)
    data["pillars"][2]["rating"] = None
    _, out = structured.extract("fundamentals", block(data), profile=FINANCIAL)
    assert out["pillars"][2]["rating"] is None


class Repairer:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def invoke(self, messages):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return AIMessage(content=self.reply)


def test_a_missing_block_is_rebuilt_from_the_report_with_one_call():
    fixer = Repairer(json.dumps(OUTLOOK))
    body, data = structured.extract("outlook", "## Sector\nx", repair_llm=fixer)
    assert data["headline"] == OUTLOOK["headline"] and fixer.calls == 1 and body == "## Sector\nx"


def test_a_fenced_repair_reply_is_accepted():
    _, data = structured.extract("outlook", "x", repair_llm=Repairer(block(OUTLOOK)))
    assert data is not None


def test_repair_is_tried_once_and_failures_never_raise():
    bad = Repairer("not json")
    assert structured.extract("outlook", "x", repair_llm=bad)[1] is None and bad.calls == 1
    assert structured.extract("outlook", "x", repair_llm=Repairer(RuntimeError("boom")))[1] is None


def test_valid_blocks_make_no_repair_call():
    fixer = Repairer("{}")
    structured.extract("outlook", block(OUTLOOK), repair_llm=fixer)
    assert fixer.calls == 0


def test_instructions_name_the_shape_and_the_pillars():
    text = structured.instructions("fundamentals", GENERAL)
    assert "```json" in text and '"balance_sheet"' in text
    assert "company" in structured.instructions("outlook")


def _run(message, **kw):
    return run_agent(build_outlook_agent(FakeToolCallingModel(messages=iter([message]))), "task", **kw)


def test_run_agent_splits_the_block_when_asked():
    result = _run(AIMessage(content=f"## Sector\nx\n\n{block(OUTLOOK)}"), summary="outlook")
    assert result.text == "## Sector\nx" and result.structured["headline"] == OUTLOOK["headline"]


def test_run_agent_leaves_the_text_alone_without_summary():
    text = f"x\n{block(OUTLOOK)}"
    result = _run(AIMessage(content=text))
    assert result.text == text and result.structured is None


def test_truncated_replies_get_no_structure_and_no_repair_call():
    fixer = Repairer(json.dumps(OUTLOOK))
    result = _run(AIMessage(content="part", response_metadata={"finish_reason": "length"}),
                  summary="outlook", repair_llm=fixer)
    assert result.truncated and result.structured is None and fixer.calls == 0


NEWS = {"version": 1, "stance": "positive", "confidence": "medium", "headline": "Order wins.", "red_flags": [], "catalysts": ["buyback"]}
MACRO = {"version": 1, "stance": "neutral", "confidence": "low", "headline": "Mixed.", "red_flags": ["crude"], "themes": ["rates"]}


def _scripted(text):
    return FakeToolCallingModel(messages=iter([AIMessage(content=text)]))


def test_each_agent_node_stores_its_block_in_meta():
    from stock_agents.agents.macro_agent import macro_agent_node
    from stock_agents.agents.news_agent import news_agent_node
    from stock_agents.agents.outlook_agent import outlook_agent_node

    out = news_agent_node(_scripted("news text\n\n" + block(NEWS)))({"stock": "TCS"})
    assert out["news_report"] == "news text" and out["news_meta"]["structured"]["catalysts"] == ["buyback"]
    out = macro_agent_node(_scripted("macro text\n\n" + block(MACRO)))({})
    assert out["macro_report"] == "macro text" and out["macro_meta"]["structured"]["themes"] == ["rates"]
    out = outlook_agent_node(_scripted("outlook text\n\n" + block(OUTLOOK)))({"stock": "TCS"})
    assert out["outlook_report"] == "outlook text" and out["outlook_meta"]["structured"]["headline"] == OUTLOOK["headline"]


def test_prompts_ask_for_the_block():
    from stock_agents.agents.macro_agent import SYSTEM_PROMPT as macro
    from stock_agents.agents.news_agent import SYSTEM_PROMPT as news
    from stock_agents.agents.outlook_agent import SYSTEM_PROMPT as outlook

    for prompt in (news, macro, outlook):
        assert "```json" in prompt


# -- plain-language evidence with the technical detail kept for a tooltip -------------------------

def test_pillar_detail_is_optional_and_kept_when_given():
    data = fundamentals()
    data["pillars"][3].update(evidence="Earned ₹272 Cr profit but lost ₹105 Cr in cash", detail="CFO/PAT -0.4x; FY26 CFO -105 Cr")
    _, out = structured.extract("fundamentals", block(data), profile=GENERAL)
    cash = next(p for p in out["pillars"] if p["key"] == "cash_flow")
    assert cash["evidence"].startswith("Earned") and cash["detail"] == "CFO/PAT -0.4x; FY26 CFO -105 Cr"
    assert all(p["detail"] == "" for p in out["pillars"] if p["key"] != "cash_flow")  # older-style blocks still validate


def test_overlong_detail_is_rejected():
    data = fundamentals()
    data["pillars"][0]["detail"] = "x" * 201
    assert structured.extract("fundamentals", block(data), profile=GENERAL)[1] is None


def test_prompt_asks_for_plain_evidence_and_technical_detail():
    text = structured.instructions("fundamentals", GENERAL)
    assert '"detail"' in text and "plain-language" in text and "first-time retail investor" in text
    for jargon in ("CFO", "ROCE", "P/E"):
        assert jargon in text  # named only so the model knows to translate them
    assert "plain-language" not in structured.instructions("news")
