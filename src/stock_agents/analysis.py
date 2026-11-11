"""Run the whole workflow for one stock and store the result: shared by the CLI and the web UI."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from typing import Callable

from stock_agents.config import LLMConfig, reuse_hours
from stock_agents.graph import run_workflow
from stock_agents.storage import Report, Repository
from stock_agents.storage.repository import utcnow

Notify = Callable[[str], None]
Progress = Callable[[str, str], None]  # (stage, state)

STAGES = ("news", "macro", "fundamentals", "outlook")
# stage state: waiting | running | reused | done | failed
NODE_STAGE = {"news_agent": "news", "macro_agent": "macro", "fundamentals_agent": "fundamentals",
              "outlook_agent": "outlook"}


@contextmanager
def tracked_run(conn: sqlite3.Connection, command: str, stock: str | None, cfg: LLMConfig, run_id: int | None = None):
    """Record a run (or finish one already started with `run_id`); it is marked failed, with the error,
    if the body raises."""
    repo = Repository(conn)
    if run_id is None:
        with conn:
            run_id = repo.start_run(command, stock, provider=cfg.provider, model=cfg.model, max_tokens=cfg.max_tokens)
    try:
        yield run_id
    except BaseException as exc:
        with conn:
            repo.finish_run(run_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    with conn:
        repo.finish_run(run_id)


def save_report(repo: Repository, run_id: int, agent: str, stock: str | None, text: str, meta: dict | None,
                notify: Notify = lambda msg: None, **links) -> int:
    meta = meta or {}
    report_id = repo.save_report(
        run_id, agent, text, stock=stock, truncated=meta.get("truncated", False),
        input_tokens=meta.get("input_tokens"), output_tokens=meta.get("output_tokens"), **links,
    )
    note = " (INCOMPLETE: hit the token limit)" if meta.get("truncated") else ""
    notify(f"Saved {agent} report #{report_id}{note}")
    return report_id


def reusable(repo: Repository, agent: str, stock: str | None, hours: float | None,
             notify: Notify = lambda msg: None) -> Report | None:
    """Latest complete stored report younger than the allowed age (`hours`, else config); 0 disables."""
    hours = hours if hours is not None else reuse_hours(agent)
    if hours <= 0:
        return None
    report = repo.latest(agent, stock, max_age=timedelta(hours=hours))
    if report:
        age = utcnow() - report.created_at
        notify(f"Reusing {agent} report #{report.id} ({age.total_seconds() / 3600:.1f}h old, limit {hours:g}h)")
    return report


def run_all(
    stock: str, conn: sqlite3.Connection, repo: Repository, cfg: LLMConfig, llm, *,
    max_news_age: float | None = None, max_macro_age: float | None = None,
    max_fundamentals_age: float | None = None, run_id: int | None = None,
    progress: Progress | None = None, notify: Notify = lambda msg: None, workflow=None,
) -> dict:
    """Run news, macro, fundamentals and outlook for `stock` and store the reports in one run.

    Recent stored reports are reused instead of re-running their agent (limits in hours; None means the
    configured default, 0 means always re-run). `progress(stage, state)` is called as stages change.
    Returns the workflow result with the saved report ids under `saved` and the reused reports' ids.
    """
    workflow = workflow or run_workflow
    news_old = reusable(repo, "news", stock, max_news_age, notify)
    macro_old = reusable(repo, "macro", None, max_macro_age, notify)
    fund_old = reusable(repo, "fundamentals", stock, max_fundamentals_age, notify)

    def emit(stage: str, state: str) -> None:
        if progress:
            progress(stage, state)

    for stage, old in (("news", news_old), ("macro", macro_old), ("fundamentals", fund_old)):
        emit(stage, "reused" if old else "running")
    emit("outlook", "waiting")

    with tracked_run(conn, "all", stock, cfg, run_id) as run_id:
        kwargs = {}
        if progress:  # only the real workflow reports progress
            pending = {s for s, old in (("news", news_old), ("macro", macro_old), ("fundamentals", fund_old))}

            def node_done(node: str) -> None:
                stage = NODE_STAGE[node]
                if stage != "outlook":
                    pending.discard(stage)
                    emit(stage, "reused" if {"news": news_old, "macro": macro_old, "fundamentals": fund_old}[stage]
                         else "done")
                    if not pending:
                        emit("outlook", "running")

            kwargs["progress"] = node_done
        result = workflow(
            stock, llm,
            news_report=news_old.content_md if news_old else None,
            macro_report=macro_old.content_md if macro_old else None,
            fundamentals_report=fund_old.content_md if fund_old else None,
            **kwargs,
        )
        saved: dict[str, int] = {}
        with conn:
            saved["news"] = news_old.id if news_old else save_report(
                repo, run_id, "news", stock, result["news_report"], result.get("news_meta"), notify)
            saved["macro"] = macro_old.id if macro_old else save_report(
                repo, run_id, "macro", None, result["macro_report"], result.get("macro_meta"), notify)
            if fund_old:
                saved["fundamentals"] = fund_old.id
            elif result.get("fundamentals_report"):
                saved["fundamentals"] = save_report(
                    repo, run_id, "fundamentals", stock, result["fundamentals_report"],
                    result.get("fundamentals_meta"), notify)
            else:  # data unavailable: the outlook still runs, without it
                notify(f"Warning: no fundamentals report - {result.get('fundamentals_error', 'unknown error')}")
                emit("fundamentals", "failed")
            saved["outlook"] = save_report(
                repo, run_id, "outlook", stock, result["outlook_report"], result.get("outlook_meta"), notify,
                news_report_id=saved["news"], macro_report_id=saved["macro"],
                fundamentals_report_id=saved.get("fundamentals"))
        emit("outlook", "done")
    return {**result, "run_id": run_id, "saved": saved}
