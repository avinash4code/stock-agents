"""Run the same stocks through different model setups and write the reports out side by side.

A setup says which model each agent uses, e.g. `news=deepseek:deepseek-flash,outlook=anthropic:claude-sonnet-5-5`
(`all=` sets every agent; agents not named use the `LLM_*` defaults from `.env`). Nothing is stored in the
database: the point is to read the reports and judge them, so each run is a fresh one that never reuses a stored report.
"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from stock_agents.config import AGENTS, LLMConfig
from stock_agents.graph import run_workflow
from stock_agents.llm import build_llm

Notify = Callable[[str], None]

# (title, report key, meta key) for each section of a run, in reading order.
SECTIONS = (
    ("News", "news_report", "news_meta"),
    ("Macro", "macro_report", "macro_meta"),
    ("Fundamentals", "fundamentals_report", "fundamentals_meta"),
    ("Outlook", "outlook_report", "outlook_meta"),
)


@dataclass
class Outcome:
    """One stock run through one setup: the workflow result, or the error that stopped it."""

    result: dict | None = None
    error: str | None = None
    seconds: float = 0.0

    def tokens(self, meta_key: str) -> tuple[int | None, int | None]:
        meta = (self.result or {}).get(meta_key) or {}
        return meta.get("input_tokens"), meta.get("output_tokens")

    def total_tokens(self) -> tuple[int, int]:
        ins = outs = 0
        for _, _, meta_key in SECTIONS:
            i, o = self.tokens(meta_key)
            ins, outs = ins + (i or 0), outs + (o or 0)
        return ins, outs


@dataclass
class Comparison:
    stocks: list[str]
    setups: list[str]  # labels, in the order given
    outcomes: dict[tuple[str, str], Outcome] = field(default_factory=dict)  # (stock, setup) -> outcome


# Compared when no --setup is given: DeepSeek Flash on the tool-using agents, and either Sonnet 5.5 or DeepSeek
# V4-Pro on the two that write the analysis.
DEFAULT_SETUPS = {
    "flash+sonnet": "news=deepseek:deepseek-flash,macro=deepseek:deepseek-flash,"
                    "fundamentals=anthropic:claude-sonnet-5-5,outlook=anthropic:claude-sonnet-5-5",
    "flash+pro": "news=deepseek:deepseek-flash,macro=deepseek:deepseek-flash,"
                 "fundamentals=deepseek:deepseek-v4-pro,outlook=deepseek:deepseek-v4-pro",
}


def describe_setup(cfg: LLMConfig) -> str:
    return ", ".join(f"{a}={cfg.describe_agent(a)}" for a in AGENTS)


def parse_setup(spec: str, base: LLMConfig) -> LLMConfig:
    """`agent=provider:model@temperature,...` -> a config whose agents use those models (`provider:` may be left
    out to keep the base provider, `@temperature` to keep the default one, and either part alone is fine, e.g.
    `outlook=@0.3`; `all` covers every agent). Raises ValueError on a malformed spec."""
    chosen: dict[str, LLMConfig] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        agent, sep, target = part.partition("=")
        agent, target = agent.strip().lower(), target.strip()
        if not sep or not target:
            raise ValueError(f"bad setup entry {part!r}: expected agent=provider:model")
        if agent != "all" and agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r} in {part!r}; choose from {', '.join(AGENTS)} or all")
        target, at, temp_text = target.partition("@")
        try:
            temperature = float(temp_text) if at else None
        except ValueError:
            raise ValueError(f"bad temperature {temp_text!r} in {part!r}") from None
        provider, colon, model = target.partition(":")
        if not colon:  # no provider given: a bare name is a model of the base provider
            provider, model = "", target
        try:
            cfg = base.derive(provider=provider or None, model=model or None, temperature=temperature,
                              source=f"provider in {part!r}")
        except RuntimeError as exc:
            raise ValueError(str(exc)) from None
        for name in AGENTS if agent == "all" else (agent,):
            chosen[name] = cfg
    return base.with_agent_models(chosen)


def run_comparison(
    stocks: list[str], setups: dict[str, LLMConfig], *, notify: Notify = lambda msg: None,
    workflow=run_workflow, llm_builder=build_llm,
) -> Comparison:
    """Run every stock through every setup (one at a time) and collect the outcomes; a failing run is recorded, not raised."""
    comparison = Comparison(list(stocks), list(setups))
    models = {label: None for label in setups}
    for stock in stocks:
        for label, cfg in setups.items():
            notify(f"Running {stock} with setup {label!r} ...")
            started = time.monotonic()
            try:
                if models[label] is None:
                    models[label] = llm_builder(cfg)
                outcome = Outcome(result=workflow(stock, models[label]))
            except Exception as exc:  # noqa: BLE001 - one bad run must not lose the others
                logging.getLogger(__name__).exception("comparison run failed")
                outcome = Outcome(error=f"{type(exc).__name__}: {exc}")
            outcome.seconds = time.monotonic() - started
            comparison.outcomes[(stock, label)] = outcome
            notify(f"  {'FAILED: ' + outcome.error if outcome.error else 'done'} ({outcome.seconds:.0f}s)")
    return comparison


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "x"


def _count(value) -> str:
    return "-" if value is None else f"{value:,}"


def _report_cell(outcome: Outcome, report_key: str, meta_key: str) -> tuple[str, str]:
    """(text, one-line facts) for a section of one outcome."""
    if outcome.error:
        return f"FAILED: {outcome.error}", "failed"
    result = outcome.result or {}
    text = result.get(report_key)
    if not text:
        return f"(none) {result.get('fundamentals_error', '')}".strip() if report_key == "fundamentals_report" else "(none)", "no report"
    ins, outs = outcome.tokens(meta_key)
    meta = result.get(meta_key) or {}
    return text, f"{len(text):,} chars, tokens in/out {_count(ins)}/{_count(outs)}" + (
        ", INCOMPLETE (hit the token limit)" if meta.get("truncated") else "")


def summary_markdown(comparison: Comparison, setup_models: dict[str, str]) -> str:
    lines = ["# Model comparison", "", "## Setups", ""]
    lines += [f"- **{label}**: {setup_models[label]}" for label in comparison.setups]
    lines += ["", "## Totals per run", "",
              "| Stock | Setup | Time | Tokens in | Tokens out | Status |", "|---|---|---|---|---|---|"]
    for stock in comparison.stocks:
        for label in comparison.setups:
            o = comparison.outcomes[(stock, label)]
            ins, outs = o.total_tokens()
            flags = [k for _, k, m in SECTIONS if (o.result or {}).get(m, {}).get("truncated")]
            status = f"FAILED: {o.error}" if o.error else ("INCOMPLETE: " + ", ".join(flags) if flags else "ok")
            lines.append(f"| {stock} | {label} | {o.seconds:.0f}s | {ins:,} | {outs:,} | {status} |")
    return "\n".join(lines) + "\n"


def stock_markdown(stock: str, comparison: Comparison) -> str:
    lines = [f"# {stock}", ""]
    for title, report_key, meta_key in SECTIONS:
        lines += [f"## {title}", ""]
        for label in comparison.setups:
            text, facts = _report_cell(comparison.outcomes[(stock, label)], report_key, meta_key)
            lines += [f"### {label}  ({facts})", "", text, ""]
    return "\n".join(lines)


_CSS = """body{font:14px/1.5 system-ui,sans-serif;margin:1.5rem;max-width:1800px}
h2{margin-top:2rem;border-bottom:1px solid #ccc}
.grid{display:grid;gap:1rem;align-items:start}
.col{border:1px solid #ddd;border-radius:6px;padding:.6rem;min-width:0}
.col h3{margin:.1rem 0}.facts{color:#666;font-size:12px;margin-bottom:.4rem}
pre{white-space:pre-wrap;word-wrap:break-word;margin:0;font:13px/1.45 ui-monospace,monospace}"""


def stock_html(stock: str, comparison: Comparison) -> str:
    """One page per stock: for each agent, the setups' reports in columns next to each other."""
    cols = f"grid-template-columns:repeat({len(comparison.setups)},minmax(0,1fr))"
    parts = [f"<!doctype html><meta charset=utf-8><title>{html.escape(stock)} - model comparison</title>",
             f"<style>{_CSS}</style><h1>{html.escape(stock)}</h1>"]
    for title, report_key, meta_key in SECTIONS:
        parts.append(f"<h2>{title}</h2><div class=grid style='{cols}'>")
        for label in comparison.setups:
            text, facts = _report_cell(comparison.outcomes[(stock, label)], report_key, meta_key)
            parts.append(f"<div class=col><h3>{html.escape(label)}</h3><div class=facts>{html.escape(facts)}</div>"
                         f"<pre>{html.escape(text)}</pre></div>")
        parts.append("</div>")
    return "".join(parts)


def write_comparison(comparison: Comparison, setup_models: dict[str, str], directory: str | Path) -> Path:
    """Write summary.md plus `<stock>.md` and `<stock>.html` for each stock into a new timestamped folder."""
    out = Path(directory) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(summary_markdown(comparison, setup_models), encoding="utf-8")
    for stock in comparison.stocks:
        name = _slug(stock)
        (out / f"{name}.md").write_text(stock_markdown(stock, comparison), encoding="utf-8")
        (out / f"{name}.html").write_text(stock_html(stock, comparison), encoding="utf-8")
    return out
