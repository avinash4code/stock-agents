"""The small structured block every agent returns next to its markdown report.

The model ends its reply with one fenced ```json block; `extract` splits it off, validates it and, if it is
missing or invalid, makes one repair call. A bad block never fails a report: the structure is just None and
the UI shows no card for it.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from stock_agents import prompts
from stock_agents.pillars import PillarProfile, prompt_block

logger = logging.getLogger(__name__)

VERSION = 1

Stance = Literal["positive", "neutral", "negative"]
Confidence = Literal["low", "medium", "high"]


def _first_three(value):
    return value[:3] if isinstance(value, list) else value


Flags = Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], BeforeValidator(_first_three)]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = VERSION


class _Summary(_Base):
    stance: Stance
    confidence: Confidence
    headline: str = Field(min_length=1, max_length=200)
    red_flags: Flags = []


class NewsSummary(_Summary):
    catalysts: Flags = []


class MacroSummary(_Summary):
    themes: Flags = []


class Pillar(_Base):
    key: str
    label: str = ""  # filled in from the profile, never taken from the model
    rating: int | None = Field(default=None, ge=1, le=5)
    evidence: str = Field(default="", max_length=160)  # plain language, shown on the card
    detail: str = Field(default="", max_length=200)  # the technical figures behind it, shown in a tooltip


class FundamentalsSummary(_Summary):
    profile: str = ""  # filled in from the profile
    pillars: list[Pillar]
    strengths: Flags = []
    watch_next_quarter: Flags = []

    @model_validator(mode="after")
    def _pillars_match_the_profile(self, info: ValidationInfo):
        profile: PillarProfile | None = (info.context or {}).get("profile")
        if profile is None:
            return self
        got = sorted(p.key for p in self.pillars)
        if got != sorted(profile.keys):
            raise ValueError(f"pillars must be exactly {profile.keys}, got {[p.key for p in self.pillars]}")
        by_key = {p.key: p for p in self.pillars}
        self.pillars = [by_key[k].model_copy(update={"label": profile.label(k)}) for k in profile.keys]
        self.profile = profile.name
        return self


class Horizon(_Base):
    stance: Stance
    confidence: Confidence


class Horizons(_Base):
    short: Horizon
    medium: Horizon
    long: Horizon


class OutlookSummary(_Base):
    headline: str = Field(min_length=1, max_length=200)
    company: Horizons
    sector: Horizons


MODELS: dict[str, type[_Base]] = {
    "news": NewsSummary, "macro": MacroSummary, "fundamentals": FundamentalsSummary, "outlook": OutlookSummary,
}

_COMMON = (
    '"version": 1, "stance": "positive|neutral|negative", "confidence": "low|medium|high", '
    '"headline": "one sentence, at most 25 words"'
)
_SHAPES = {
    "news": '{' + _COMMON + ', "red_flags": ["up to 3 short items"], "catalysts": ["up to 3 short items"]}',
    "macro": '{' + _COMMON + ', "red_flags": ["up to 3 short risks"], "themes": ["up to 3 short themes"]}',
    "fundamentals": '{' + _COMMON + ', "pillars": [{"key": "...", "rating": 1-5 or null, "evidence": "plain-language '
                    'sentence, at most 15 words", "detail": "the technical figures behind it, at most 20 words"}], "red_flags": ["top 3, short"], "strengths": ["top 3, short"], '
                    '"watch_next_quarter": ["up to 3 quantified thresholds"]}',
    "outlook": '{"version": 1, "headline": "one sentence, at most 25 words", '
               '"company": {"short": H, "medium": H, "long": H}, "sector": {"short": H, "medium": H, "long": H}} '
               'where H is {"stance": "positive|neutral|negative", "confidence": "low|medium|high"}',
}


PLAIN_EVIDENCE = (
    'Write each pillar\'s "evidence" for a first-time retail investor: one plain sentence saying what is happening '
    "to the business or to the shareholder, using rupee amounts from the tables (write them as ₹272 Cr in this block only) "
    "and no abbreviations or ratios. Say it in everyday words: for example \"cash from operations\" instead of CFO, "
    '"profit" instead of PAT, "what the company owes" instead of debt-to-equity, "return on money invested" instead of '
    'ROCE, "price compared with profit" instead of P/E, "foreign funds" and "Indian institutions" instead of FII and DII. '
    'Put the technical figures in "detail" instead, e.g. ratios, growth rates and the table rows they come from. '
    "Examples of the style (illustrative numbers only; use the real figures): "
    '"Earned ₹80 Cr profit but only ₹30 Cr of it arrived as cash"; '
    '"Owes little: borrowings are a tenth of what shareholders own"; '
    '"Shares cost more than similar companies relative to profit". '
    "Never copy the example numbers."
)


def instructions(agent: str, profile: PillarProfile | None = None) -> str:
    """The paragraph appended to an agent's prompt, asking for the block."""
    text = (
        "After the report, end your reply with one fenced ```json block (and nothing after it) that restates your "
        f"conclusions in exactly this shape:\n{_SHAPES[agent]}\n"
        "The block must agree with the report. Use only the keys shown; use plain text in strings."
    )
    if agent == "fundamentals":
        text += "\n" + PLAIN_EVIDENCE
    if agent == "outlook":
        text += " The company stances are the overall verdict for each horizon; the headline is the one-line takeaway."
    if profile is not None:
        text += "\n" + prompt_block(profile)
    return text


_FENCE = re.compile(r"```json[ \t]*\n(.*?)\n[ \t]*```", re.S | re.I)


def split_block(text: str) -> tuple[str, str | None]:
    """The report without its last ```json block, and that block's text (None if there is none)."""
    matches = list(_FENCE.finditer(text))
    if not matches:
        return text.strip(), None
    last = matches[-1]
    return (text[: last.start()] + text[last.end():]).strip(), last.group(1)


def validate(agent: str, raw: str, profile: PillarProfile | None = None) -> dict:
    """Parse and validate a block; raises ValueError (including pydantic's) when it is not acceptable."""
    data = json.loads(raw)
    model = MODELS[agent].model_validate(data, context={"profile": profile})
    return model.model_dump()


REPAIR_SYSTEM = (
    "You turn an analyst report into a small JSON object. Reply with the JSON only, no prose and no code fence. "
    "Take every judgement from the report; do not add new views."
)


def _repair(llm: BaseChatModel, agent: str, body: str, bad: str | None, error: str, profile: PillarProfile | None) -> str | None:
    prompt = (
        f"Report:\n<report>\n{body}\n</report>\n\nWrite the JSON for it in exactly this shape:\n{_SHAPES[agent]}\n"
        + (f"\n{prompt_block(profile)}\n" if profile is not None else "")
        + (f"\nA previous attempt was rejected ({error}):\n{bad}\n" if bad else "")
    )
    try:
        repair = prompts.resolve("repair", REPAIR_SYSTEM)
        reply = prompts.linked(llm, repair).invoke([SystemMessage(repair.text), HumanMessage(prompt)])
    except Exception as exc:  # a failed repair must not fail the report
        logger.warning("Structured-summary repair call failed for %s: %s", agent, exc)
        return None
    content = reply.content
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    raw = content.strip()
    fence = _FENCE.search(raw)
    return fence.group(1) if fence else raw


def extract(
    agent: str, text: str, *, profile: PillarProfile | None = None, repair_llm: BaseChatModel | None = None,
) -> tuple[str, dict | None]:
    """Split a reply into (markdown, structured). `structured` is None when no valid block could be had."""
    body, data, _ = extract_detailed(agent, text, profile=profile, repair_llm=repair_llm)
    return body, data


def extract_detailed(
    agent: str, text: str, *, profile: PillarProfile | None = None, repair_llm: BaseChatModel | None = None,
) -> tuple[str, dict | None, bool]:
    """Like `extract`, plus whether the block only became valid through the repair call."""
    body, raw = split_block(text)
    error = "no JSON block"
    if raw is not None:
        try:
            return body, validate(agent, raw, profile), False
        except (ValueError, ValidationError) as exc:
            error = str(exc).splitlines()[0][:200]
            logger.warning("Invalid %s summary block (%s)", agent, error)
    if repair_llm is not None:
        fixed = _repair(repair_llm, agent, body, raw, error, profile)
        if fixed:
            try:
                return body, validate(agent, fixed, profile), True
            except (ValueError, ValidationError) as exc:
                logger.warning("Repaired %s summary block still invalid: %s", agent, str(exc).splitlines()[0][:200])
    return body, None, False
