"""Save agent reports as markdown files."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

DEFAULT_DIR = "news_reports"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "stock"


def _write(name: str, title: str, text: str, directory: str | Path, now: datetime | None) -> Path:
    now = now or datetime.now()
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_{now:%Y-%m-%d_%H%M}.md"
    path.write_text(f"# {title}\n\n_Generated {now:%Y-%m-%d %H:%M}_\n\n{text.strip()}\n", encoding="utf-8")
    return path


def save_report(stock: str, summary: str, directory: str | Path = DEFAULT_DIR, now: datetime | None = None) -> Path:
    """Write the news agent's report to `<directory>/<stock>_<YYYY-MM-DD_HHMM>.md`."""
    return _write(slugify(stock), f"News report: {stock}", summary, directory, now)


def save_macro_report(summary: str, directory: str | Path = DEFAULT_DIR, now: datetime | None = None) -> Path:
    """Write the macro agent's report to `<directory>/macro_<YYYY-MM-DD_HHMM>.md`."""
    return _write("macro", "Macro report: Indian markets", summary, directory, now)


def save_outlook_report(stock: str, summary: str, directory: str | Path = DEFAULT_DIR, now: datetime | None = None) -> Path:
    """Write the outlook agent's report to `<directory>/<stock>_outlook_<YYYY-MM-DD_HHMM>.md`."""
    return _write(f"{slugify(stock)}_outlook", f"Outlook: {stock}", summary, directory, now)
