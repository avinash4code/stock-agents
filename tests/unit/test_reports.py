from datetime import datetime

from stock_agents import cli
from stock_agents.reports import save_macro_report, save_outlook_report, save_report, slugify

NOW = datetime(2026, 10, 26, 14, 5)


def test_slugify():
    assert slugify("Reliance Industries Ltd.") == "reliance-industries-ltd"
    assert slugify("M&M") == "m-m"
    assert slugify("???") == "stock"


def test_save_report_creates_folder_and_file(tmp_path):
    path = save_report("TCS", "- bullet\n", tmp_path / "news_reports", now=NOW)
    assert path == tmp_path / "news_reports" / "tcs_2026-10-26_1405.md"
    text = path.read_text()
    assert text.startswith("# News report: TCS") and "- bullet" in text


def test_save_macro_report_sits_beside_the_news_report(tmp_path):
    folder = tmp_path / "news_reports"
    news = save_report("TCS", "n", folder, now=NOW)
    macro = save_macro_report("## Overall tone\nmixed", folder, now=NOW)
    assert macro == folder / "macro_2026-10-26_1405.md"
    assert macro.parent == news.parent
    assert macro.read_text().startswith("# Macro report") and "mixed" in macro.read_text()


def test_save_outlook_report_is_named_after_the_stock(tmp_path):
    path = save_outlook_report("Tata Motors", "## Sector\npositive", tmp_path, now=NOW)
    assert path == tmp_path / "tata-motors_outlook_2026-10-26_1405.md"
    assert path.read_text().startswith("# Outlook: Tata Motors")


def test_cli_saves_all_reports_into_news_reports_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli, "run_workflow", lambda stock: {"news_report": f"summary for {stock}", "macro_report": "macro view", "outlook_report": "outlook view"}
    )
    assert cli.main(["Infosys"]) == 0
    folder = tmp_path / "news_reports"
    (news,) = folder.glob("infosys_2*.md")
    (macro,) = folder.glob("macro_*.md")
    (outlook,) = folder.glob("infosys_outlook_*.md")
    assert "outlook view" in outlook.read_text()
    assert "summary for Infosys" in news.read_text() and "macro view" not in news.read_text()
    assert "macro view" in macro.read_text()
    out = capsys.readouterr().out
    assert "summary for Infosys" in out and "macro view" in out
