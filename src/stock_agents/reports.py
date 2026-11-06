"""Markdown export of stored reports, and import of reports saved by earlier versions as files."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from stock_agents.agents.common import TRUNCATION_NOTE
from stock_agents.storage import Report, Repository

DEFAULT_EXPORT_DIR = "exports"

TITLES = {"news": "News report", "macro": "Macro report: Indian markets", "outlook": "Outlook",
          "fundamentals": "Fundamentals report"}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "stock"


def render_markdown(report: Report) -> str:
    title = TITLES[report.agent]
    if report.agent != "macro":
        title = f"{title}: {report.stock}"
    when = report.created_at.astimezone()  # local time
    return f"# {title}\n\n_Generated {when:%Y-%m-%d %H:%M} (report #{report.id})_\n\n{report.content_md.strip()}\n"


def export_filename(report: Report) -> str:
    when = report.created_at.astimezone()
    name = {"macro": "macro", "news": slugify(report.stock or ""), "outlook": f"{slugify(report.stock or '')}_outlook",
            "fundamentals": f"{slugify(report.stock or '')}_fundamentals"}
    return f"{name[report.agent]}_{when:%Y-%m-%d_%H%M}.md"


def export_report(report: Report, directory: str | Path = DEFAULT_EXPORT_DIR) -> Path:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / export_filename(report)
    path.write_text(render_markdown(report), encoding="utf-8")
    return path


# -- import of the markdown files written by versions that stored reports on disk ----------

_FILE = re.compile(r"^(?P<name>.+?)_(?P<ts>\d{4}-\d{2}-\d{2}_\d{4})\.md$")
_HEADER_LINES = ("# ", "_Generated")


def read_report_body(path: str | Path) -> str:
    """Report text without the `# title` and `_Generated ..._` header lines."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    while lines and (not lines[0].strip() or lines[0].startswith(_HEADER_LINES)):
        lines.pop(0)
    return "\n".join(lines).strip()


def _stock_from_title(path: Path, fallback: str) -> str:
    first = path.read_text(encoding="utf-8").split("\n", 1)[0]
    match = re.match(r"^# (?:News report|Outlook|Fundamentals report): (.+)$", first)
    return match.group(1).strip() if match else fallback


def import_reports(repo: Repository, directory: str | Path) -> tuple[int, int]:
    """Import old `*.md` report files; returns (imported, skipped). Safe to run repeatedly."""
    imported = skipped = 0
    run_ids: dict[str, int] = {}
    for path in sorted(Path(directory).glob("*.md")):
        match = _FILE.match(path.name)
        if not match:
            skipped += 1
            continue
        name = match["name"]
        created = datetime.strptime(match["ts"], "%Y-%m-%d_%H%M").astimezone(timezone.utc)  # file names are local time
        if name == "macro":
            agent, stock = "macro", None
        elif name.endswith("_fundamentals"):
            agent, stock = "fundamentals", _stock_from_title(path, name[: -len("_fundamentals")])
        elif name.endswith("_outlook"):
            agent, stock = "outlook", _stock_from_title(path, name[: -len("_outlook")])
        else:
            agent, stock = "news", _stock_from_title(path, name)
        if repo.exists(agent, stock, created):
            skipped += 1
            continue
        if "import" not in run_ids:
            run_ids["import"] = repo.start_run("import", None)
        body = read_report_body(path)
        repo.save_report(  # an empty body means the model ran out of tokens before writing anything
            run_ids["import"], agent, body, stock=stock, truncated=TRUNCATION_NOTE in body or not body,
            created_at=created,
        )
        imported += 1
    if "import" in run_ids:
        repo.finish_run(run_ids["import"])
    return imported, skipped
