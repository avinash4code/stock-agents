"""Optional Langfuse tracing and scoring.

Everything here is a no-op unless `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set (and
`LANGFUSE_ENABLED` is not false), so the CLI, the web UI and the tests behave as before without it. A tracing
failure is logged and never fails an analysis.

`trace()` opens one Langfuse trace around a run and makes its LangChain callback handler the ambient config, so
every graph node, LLM call and tool call made inside it (including the structured-summary repair calls) is nested
under that trace without being passed anything.
"""
from __future__ import annotations

import logging
import os
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_HOST = "https://cloud.langfuse.com"
_FALSE = {"0", "false", "no", "off"}

_current: ContextVar["Trace | None"] = ContextVar("stock_agents_langfuse_trace", default=None)


def _load_env() -> None:
    """Make sure `.env` has been read (the CLI and web UI normally do this through the LLM config)."""
    try:
        from stock_agents.config import _load_env_file

        _load_env_file()
    except Exception:  # noqa: BLE001 - tracing is optional
        pass


def enabled() -> bool:
    _load_env()
    if os.environ.get("LANGFUSE_ENABLED", "true").strip().lower() in _FALSE:
        return False
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def host() -> str:
    return (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or DEFAULT_HOST).rstrip("/")


def trace_url(trace_id: str | None) -> str | None:
    """Link to a trace in the Langfuse UI (Langfuse redirects /trace/<id> to the project it belongs to)."""
    return f"{host()}/trace/{trace_id}" if trace_id and enabled() else None


class Trace:
    """The open trace of a run: its id, and a way to attach scores to it."""

    def __init__(self, client, span, trace_id: str):
        self._client = client
        self._span = span
        self.trace_id = trace_id

    def score(self, name: str, value: bool | int | float, comment: str | None = None) -> None:
        try:
            kind = "BOOLEAN" if isinstance(value, bool) else "NUMERIC"
            self._client.create_score(
                trace_id=self.trace_id, name=name, value=float(value), data_type=kind, comment=comment)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse score %s failed: %s", name, exc)

    def finish(self, status: str, error: str | None = None) -> None:
        try:
            if error:
                self._span.update(level="ERROR", status_message=error[:500], output={"status": status})
            else:
                self._span.update(output={"status": status})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse trace update failed: %s", exc)


def current() -> Trace | None:
    """The trace opened by the innermost enclosing `trace()`, if tracing is on."""
    return _current.get()


def _tags(stock: str | None, command: str, models: dict[str, str]) -> list[str]:
    tags = [f"command:{command}"]
    if stock:
        tags.append(f"stock:{stock}"[:200])
    tags += [f"model:{m}"[:200] for m in sorted(set(models.values()))]
    return tags


@contextmanager
def trace(command: str, stock: str | None, run_id: int, models: dict[str, str] | None = None) -> Iterator[Trace | None]:
    """Trace one run. Yields the `Trace`, or None when tracing is off or could not be set up.

    `models` maps agent name to "provider/model" and is recorded as trace metadata and tags.
    """
    if not enabled():
        yield None
        return
    models = models or {}
    stack = ExitStack()
    tr: Trace | None = None
    token = None
    try:
        from langchain_core.runnables.config import ensure_config, var_child_runnable_config
        from langfuse import Langfuse, get_client, propagate_attributes
        from langfuse.langchain import CallbackHandler

        client = get_client()
        trace_id = Langfuse.create_trace_id()
        name = f"{command} {stock}" if stock else command
        span = stack.enter_context(client.start_as_current_observation(
            trace_context={"trace_id": trace_id}, name=name, as_type="chain", input={"stock": stock, "command": command}))
        stack.enter_context(propagate_attributes(
            session_id=f"run-{run_id}", trace_name=name, tags=_tags(stock, command, models),
            metadata={"run_id": str(run_id), **{f"model_{a}": m[:200] for a, m in models.items()}}))
        # `set_config_context` only applies inside a copied context, so set the ambient config directly.
        config_token = var_child_runnable_config.set(ensure_config({"callbacks": [CallbackHandler()]}))
        stack.callback(var_child_runnable_config.reset, config_token)
        tr = Trace(client, span, trace_id)
        token = _current.set(tr)
    except Exception as exc:  # noqa: BLE001 - never let tracing break an analysis
        logger.warning("Langfuse tracing disabled for this run: %s", exc)
        _close(stack)
        stack = ExitStack()
        tr = None
    try:
        yield tr
    except BaseException as exc:
        if tr:
            tr.finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    else:
        if tr:
            tr.finish("ok")
    finally:
        if token is not None:
            _current.reset(token)
        _close(stack)


def _close(stack: ExitStack) -> None:
    try:
        stack.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse trace close failed: %s", exc)


def score_report(agent: str, meta: dict[str, Any] | None, *, reused: bool = False) -> None:
    """Attach the quality signals of one agent's report to the current trace, as `<agent>.<signal>` scores."""
    tr = current()
    if tr is None:
        return
    tr.score(f"{agent}.reused", reused)
    if reused or meta is None:
        return
    tr.score(f"{agent}.structured_valid", bool(meta.get("structured")))
    tr.score(f"{agent}.structured_repaired", bool(meta.get("repaired")))
    tr.score(f"{agent}.truncated", bool(meta.get("truncated")))
    for key in ("input_tokens", "output_tokens"):
        if meta.get(key) is not None:
            tr.score(f"{agent}.{key}", meta[key])


def score_unavailable(agent: str, reason: str) -> None:
    """An agent produced no report at all (e.g. the fundamentals data could not be fetched)."""
    tr = current()
    if tr is not None:
        tr.score(f"{agent}.unavailable", True, comment=reason[:500])


def flush() -> None:
    """Send any buffered events now (short-lived processes would otherwise drop them)."""
    if not enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse flush failed: %s", exc)
