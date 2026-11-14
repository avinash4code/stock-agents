"""Background analysis runs for the web UI.

A run is a row in `runs` (status `running` until it finishes) plus an in-memory record of per-stage
progress, which is lost on restart: runs left `running` by a dead server are marked failed on startup.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Callable

from stock_agents.analysis import STAGES, run_all
from stock_agents.config import LLMConfig
from stock_agents.llm import build_llm
from stock_agents.storage import Repository, connect, stock_key
from stock_agents.storage.repository import utcnow

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=30)


def default_llm_factory() -> tuple[LLMConfig, object]:
    cfg = LLMConfig.from_env()
    return cfg, build_llm(cfg)


class AlreadyRunning(Exception):
    def __init__(self, run_id: int):
        super().__init__(f"an analysis of this stock is already running (run #{run_id})")
        self.run_id = run_id


class JobManager:
    def __init__(self, db_file: str | Path, llm_factory: Callable[[], tuple[LLMConfig, object]] = default_llm_factory,
                 max_workers: int = 2, workflow=None):
        self.db_file = db_file
        self.llm_factory = llm_factory
        self.workflow = workflow
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="analysis")
        self._lock = threading.Lock()
        self._stages: dict[int, dict[str, str]] = {}
        self._active: dict[str, int] = {}  # stock key -> run id
        self._mark_stale_runs()

    def _mark_stale_runs(self) -> None:
        conn = connect(self.db_file)
        try:
            repo = Repository(conn)
            with conn:
                for run in repo.list_runs(limit=500):
                    if run.status == "running" and utcnow() - run.started_at > STALE_AFTER:
                        repo.finish_run(run.id, "failed", "interrupted: the server stopped before it finished")
        finally:
            conn.close()

    def stages(self, run_id: int) -> dict[str, str] | None:
        with self._lock:
            stages = self._stages.get(run_id)
            return dict(stages) if stages is not None else None

    def start(self, stock: str, **ages: float | None) -> int:
        """Record a run and start it in the background; returns the run id."""
        key = stock_key(stock)
        cfg, llm = self.llm_factory()  # fails fast (RuntimeError) if no API key is configured
        with self._lock:
            if key in self._active:
                raise AlreadyRunning(self._active[key])
            conn = connect(self.db_file)
            try:
                with conn:
                    run_id = Repository(conn).start_run(
                        "all", stock, provider=cfg.provider, model=cfg.model, max_tokens=cfg.max_tokens)
            finally:
                conn.close()
            self._active[key] = run_id
            self._stages[run_id] = {s: "waiting" for s in STAGES}
        self._pool.submit(self._run, run_id, key, stock, cfg, llm, ages)
        return run_id

    def _progress(self, run_id: int):
        def update(stage: str, state: str) -> None:
            with self._lock:
                self._stages[run_id][stage] = state
        return update

    def _run(self, run_id: int, key: str, stock: str, cfg: LLMConfig, llm, ages: dict) -> None:
        conn = connect(self.db_file)
        try:
            run_all(stock, conn, Repository(conn), cfg, llm, run_id=run_id, progress=self._progress(run_id),
                    workflow=self.workflow, **ages)  # records the failure on the run if this raises
        except BaseException:  # noqa: BLE001 - already stored on the run by tracked_run
            logger.exception("analysis of %s (run #%s) failed", stock, run_id)
            with self._lock:
                for stage, state in self._stages[run_id].items():
                    if state in ("running", "waiting"):
                        self._stages[run_id][stage] = "failed"
        finally:
            conn.close()
            with self._lock:
                self._active.pop(key, None)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
