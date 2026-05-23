"""Tests for core/report.py."""

from pathlib import Path
from typing import Any

from agents.state import FixerState
from core.egp_mapper import ErrorMapping
from core.log_parser import SASError
from core.report import generate_report, write_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_error(
    message: str = "Variable X not found.",
    line: int | None = 4,
    source: str | None = "x = salry;",
    macro: bool = False,
) -> SASError:
    return SASError(
        severity="ERROR",
        message=message,
        log_line_number=line,
        echoed_source_line=source,
        is_macro_generated=macro,
        raw_block="",
    )


def _make_mapping(
    node_id: str = "node001",
    node_code: str = "data work.out;\n    x = salry;\nrun;\n",
) -> ErrorMapping:
    return ErrorMapping(
        error=_make_error(),
        node_id=node_id,
        node_name="My Node",
        xml_file="project.xml",
        local_line=2,
        code_context="",
        node_code=node_code,
    )


def _make_state(
    *,
    log_path: str = "sample.log",
    egp_path: str = "project.egp",
    output_path: str | None = None,
    errors: list[SASError] | None = None,
    mappings: list[ErrorMapping] | None = None,
    patches: list[dict[str, Any]] | None = None,
    run_log: list[str] | None = None,
    errors_encountered: list[str] | None = None,
) -> FixerState:
    return FixerState(
        log_path=log_path,
        egp_path=egp_path,
        dry_run=False,
        parse_only=False,
        errors=errors or [],
        mappings=mappings or [],
        diagnoses=[],
        patches=patches or [],
        output_path=output_path,
        run_log=run_log or [],
        errors_encountered=errors_encountered or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_report_contains_header() -> None:
    report = generate_report(_make_state())
    assert "SAS EGP FIXER" in report


def test_report_contains_pipeline_log() -> None:
    state = _make_state(run_log=["parse_log: found 2 error(s)"])
    report = generate_report(state)
    assert "parse_log: found 2 error(s)" in report


def test_report_contains_errors() -> None:
    error = _make_error(message="Variable SALRY not found.")
    state = _make_state(errors=[error])
    report = generate_report(state)
    assert "Variable SALRY not found." in report


def test_report_patch_section_present() -> None:
    mapping = _make_mapping()
    patch = {
        "node_id": "node001",
        "xml_file": "project.xml",
        "new_code": mapping.node_code,
        "explanation": "Fixed typo",
    }
    state = _make_state(patches=[patch], mappings=[mapping])
    report = generate_report(state)
    assert "PATCHES APPLIED" in report


def test_report_diff_shows_change() -> None:
    original_code = "data work.out;\n    x = salry;\nrun;\n"
    fixed_code = "data work.out;\n    x = salary;\nrun;\n"
    mapping = _make_mapping(node_code=original_code)
    patch = {
        "node_id": "node001",
        "xml_file": "project.xml",
        "new_code": fixed_code,
        "explanation": "Renamed salry to salary",
    }
    state = _make_state(patches=[patch], mappings=[mapping])
    report = generate_report(state)
    assert "+" in report
    assert "-" in report


def test_report_no_patch_section_when_empty() -> None:
    state = _make_state(patches=[])
    report = generate_report(state)
    assert "PATCHES APPLIED" not in report


def test_report_issues_section() -> None:
    state = _make_state(errors_encountered=["something failed"])
    report = generate_report(state)
    assert "ISSUES ENCOUNTERED" in report
    assert "something failed" in report


def test_report_no_issues_section_when_empty() -> None:
    state = _make_state(errors_encountered=[])
    report = generate_report(state)
    assert "ISSUES ENCOUNTERED" not in report


def test_write_report_creates_file(tmp_path: Path) -> None:
    report_path = str(tmp_path / "test_report.txt")
    write_report(_make_state(), report_path)
    assert Path(report_path).exists()
    assert "SAS EGP FIXER" in Path(report_path).read_text(encoding="utf-8")


def test_write_report_derives_path() -> None:
    state = _make_state(output_path="sample_fixed.egp")
    report_path = write_report(state, None)
    try:
        assert report_path.endswith("_report.txt")
    finally:
        Path(report_path).unlink(missing_ok=True)
