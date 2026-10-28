from datetime import datetime, timezone

import pytest

from stock_agents.agents.common import TRUNCATION_NOTE
from stock_agents.reports import (
    export_filename, export_report, import_reports, read_report_body, render_markdown, slugify,
)
from stock_agents.storage import Repository, connect

T0 = datetime(2026, 10, 26, 14, 5, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    repo = Repository(connect(":memory:"))
    repo.run_id = repo.start_run("all", "TCS")
    return repo


def make(repo, agent, stock, text="body"):
    return repo.get(repo.save_report(repo.run_id, agent, text, stock=stock, created_at=T0))


def test_slugify():
    assert slugify("Reliance Industries Ltd.") == "reliance-industries-ltd"
    assert slugify("???") == "stock"


def test_render_markdown_titles(repo):
    news, macro, outlook = make(repo, "news", "TCS"), make(repo, "macro", None), make(repo, "outlook", "TCS")
    assert render_markdown(news).startswith("# News report: TCS")
    assert render_markdown(macro).startswith("# Macro report: Indian markets")
    assert render_markdown(outlook).startswith("# Outlook: TCS")
    assert f"(report #{news.id})" in render_markdown(news) and "body" in render_markdown(news)


def test_export_filenames_follow_the_old_scheme(repo):
    local = T0.astimezone()
    stamp = f"{local:%Y-%m-%d_%H%M}"
    assert export_filename(make(repo, "news", "Tata Motors")) == f"tata-motors_{stamp}.md"
    assert export_filename(make(repo, "macro", None)) == f"macro_{stamp}.md"
    assert export_filename(make(repo, "outlook", "Tata Motors")) == f"tata-motors_outlook_{stamp}.md"


def test_export_report_writes_file(repo, tmp_path):
    path = export_report(make(repo, "news", "TCS", "hello"), tmp_path / "out")
    assert path.parent == tmp_path / "out" and "hello" in path.read_text()


def test_read_report_body_strips_header(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("# News report: TCS\n\n_Generated 2026-10-26 14:05_\n\n## Heading\ntext\n")
    assert read_report_body(f) == "## Heading\ntext"


def _old_files(folder):
    folder.mkdir()
    (folder / "tcs_2026-10-26_1405.md").write_text("# News report: TCS\n\n_Generated x_\n\nnews body\n")
    (folder / "macro_2026-10-26_1405.md").write_text("# Macro report: Indian markets\n\n_Generated x_\n\nmacro body\n")
    (folder / "tata-motors_outlook_2026-10-26_1405.md").write_text("# Outlook: Tata Motors\n\n_Generated x_\n\nout\n")
    (folder / "tcs_outlook_2026-10-26_1500.md").write_text(f"# Outlook: TCS\n\n_Generated x_\n\n{TRUNCATION_NOTE}\n")
    (folder / "tcs_outlook_2026-10-26_1600.md").write_text("# Outlook: TCS\n\n_Generated x_\n\n")  # empty
    (folder / "notes.md").write_text("not a report")


def test_import_old_files(repo, tmp_path):
    _old_files(tmp_path / "old")
    assert import_reports(repo, tmp_path / "old") == (5, 1)
    by_agent = {(r.agent, r.stock): r for r in repo.list(limit=50)}
    assert by_agent[("news", "TCS")].content_md == "news body"
    assert by_agent[("macro", None)].content_md == "macro body"
    assert ("outlook", "Tata Motors") in by_agent  # stock name recovered from the title, not the slug
    outlooks = {r.created_at.astimezone().hour: r for r in repo.list(agent="outlook", stock="TCS")}
    assert sorted(r.truncated for r in outlooks.values()) == [True, True]  # note present, and empty body


def test_import_is_idempotent(repo, tmp_path):
    _old_files(tmp_path / "old")
    import_reports(repo, tmp_path / "old")
    assert import_reports(repo, tmp_path / "old") == (0, 6)
    assert len(repo.list(limit=50)) == 5
