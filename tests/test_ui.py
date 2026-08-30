"""§22: ui import-only smoke test, skipped when tkinter is unavailable."""

from __future__ import annotations

import pytest

from cdl.config import Settings, project_root

tkinter = pytest.importorskip("tkinter", reason="tkinter is not installed")


def test_report_text_and_html_contain_the_seven_section_facts(settings: Settings) -> None:
    from cdl.logic.check import run_check
    from cdl.ui.report import html_report, text_report
    from conftest import REFERENCE_REQUEST

    result = run_check(REFERENCE_REQUEST, settings)
    text = text_report(result)
    assert "DECISION        : Y" in text
    assert "Breakdown" in text and "buckets:" in text
    assert "reference only" in text
    html = html_report(result)
    assert "Tenor buckets" in html
    assert "Traders who have asked" in html
    assert "Today&#x27;s checks" in html or "Today's checks" in html


def test_app_module_imports_and_defines_the_seven_sections() -> None:
    from cdl.ui import app

    for method in (
        "_build_login",
        "_build_input",
        "_build_decision",
        "_build_breakdown",
        "_build_chain",
        "_build_peers",
        "_build_history",
    ):
        assert hasattr(app.LimitCheckApp, method), method


def test_no_business_logic_in_the_ui_files() -> None:
    """The window may format numbers, but must not compute a decision."""
    for name in ("app.py", "report.py"):
        text = (project_root() / "src" / "cdl" / "ui" / name).read_text(encoding="utf-8")
        assert "1 + " not in text
        assert "build_surface" not in text
        assert "lookup_ffr" not in text


def test_window_builds_when_a_display_is_available(settings: Settings) -> None:
    from cdl.ui.app import build_window

    try:
        root = build_window(settings)
    except tkinter.TclError as error:  # no display in this environment
        pytest.skip(f"no display available: {error}")
    try:
        root.update()
        assert "cross-desk-limit" in root.title()
    finally:
        root.destroy()
