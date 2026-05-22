"""Tests for agents/orchestrator.py."""

import json
import zipfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from agents.orchestrator import (
    node_apply_patches,
    node_map_errors,
    node_parse_log,
    run_pipeline,
)
from agents.state import FixerState

_DIAG_TARGET = "agents.diagnosis_agent.get_llm_client"
_PATCH_TARGET = "agents.patch_agent.get_llm_client"

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_SAMPLE_CODE = (
    "data work.salaries_clean;\n"
    "set work.employees;\n"
    "salary_new = salry * 1.1;\n"
    "run;\n"
)

_SAMPLE_LOG = (
    "NOTE: SAS Version 9.4\n"
    "     1  data work.salaries_clean;\n"
    "     2  set work.employees;\n"
    "     3  salary_new = salry * 1.1;\n"
    "     4  run;\n"
    "ERROR: Variable SALRY not found.\n"
    "     3  salary_new = salry * 1.1;\n"
)

_SAMPLE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<Project>\n"
    '  <CodeTask id="task001" name="Clean Salaries">\n'
    '    <Code id="node001">' + _SAMPLE_CODE + "</Code>\n"
    "  </CodeTask>\n"
    "</Project>\n"
)

_FIXED_CODE = (
    "data work.salaries_clean;\n"
    "set work.employees;\n"
    "salary_new = salary * 1.1;\n"
    "run;\n"
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_project(tmp_path: Path) -> dict[str, str]:
    """Write a .log file and a .egp ZIP to tmp_path; return their paths."""
    log_path = tmp_path / "sample.log"
    egp_path = tmp_path / "sample.egp"

    log_path.write_text(_SAMPLE_LOG, encoding="utf-8")

    with zipfile.ZipFile(egp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", _SAMPLE_XML)

    return {"log": str(log_path), "egp": str(egp_path)}


def _make_diagnosis_client() -> MagicMock:
    content = json.dumps(
        {
            "root_cause": "Typo in variable name",
            "diagnosis": "SALRY should be SALARY",
            "fixable": True,
            "fix_hint": "Rename salry to salary",
        }
    )
    response = MagicMock()
    response.choices[0].message.content = content
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def _make_patch_client() -> MagicMock:
    content = json.dumps(
        {
            "success": True,
            "new_code": _FIXED_CODE,
            "explanation": "Renamed salry to salary",
            "error": "",
        }
    )
    response = MagicMock()
    response.choices[0].message.content = content
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def _run_full(sample_project: dict[str, str]) -> FixerState:
    """Invoke the full pipeline with both LLM clients mocked."""
    with (
        patch(_DIAG_TARGET, return_value=_make_diagnosis_client()),
        patch(_PATCH_TARGET, return_value=_make_patch_client()),
    ):
        return run_pipeline(
            log_path=sample_project["log"],
            egp_path=sample_project["egp"],
        )


def _base_state(sample_project: dict[str, str], **overrides: Any) -> FixerState:
    data: dict[str, Any] = dict(
        log_path=sample_project["log"],
        egp_path=sample_project["egp"],
        dry_run=False,
        parse_only=False,
        errors=[],
        mappings=[],
        diagnoses=[],
        patches=[],
        output_path=None,
        run_log=[],
        errors_encountered=[],
    )
    data.update(overrides)
    return cast(FixerState, data)


# ---------------------------------------------------------------------------
# Node unit tests
# ---------------------------------------------------------------------------


def test_node_parse_log_success(sample_project: dict[str, str]) -> None:
    result = node_parse_log(_base_state(sample_project))
    assert len(result["errors"]) == 1
    assert result["errors"][0].severity == "ERROR"
    assert result["errors"][0].log_line_number == 3


def test_node_parse_log_bad_path(tmp_path: Path) -> None:
    bad = {"log": str(tmp_path / "missing.log"), "egp": ""}
    result = node_parse_log(_base_state(bad))
    assert result["errors"] == []
    assert len(result["errors_encountered"]) >= 1


def test_node_map_errors_success(sample_project: dict[str, str]) -> None:
    from core.log_parser import parse_log

    log_text = Path(sample_project["log"]).read_text(encoding="utf-8")
    parsed = parse_log(log_text)
    state = _base_state(sample_project, errors=parsed.errors)
    result = node_map_errors(state)
    assert len(result["mappings"]) == 1
    assert result["mappings"][0].node_id == "node001"


def test_node_apply_patches_dry_run(sample_project: dict[str, str]) -> None:
    patch_entry = {
        "node_id": "node001",
        "xml_file": "project.xml",
        "new_code": _FIXED_CODE,
        "explanation": "",
    }
    state = _base_state(sample_project, dry_run=True, patches=[patch_entry])
    result = node_apply_patches(state)
    assert "output_path" not in result
    assert any("skipped" in entry for entry in result["run_log"])


def test_node_apply_patches_no_patches(sample_project: dict[str, str]) -> None:
    result = node_apply_patches(_base_state(sample_project))
    assert result["output_path"] is None


# ---------------------------------------------------------------------------
# Integration tests — full run_pipeline
# ---------------------------------------------------------------------------


def test_pipeline_dry_run(sample_project: dict[str, str]) -> None:
    final = run_pipeline(
        log_path=sample_project["log"],
        egp_path=sample_project["egp"],
        dry_run=True,
    )
    assert final["diagnoses"] == []
    assert final["patches"] == []


def test_pipeline_parse_only(sample_project: dict[str, str]) -> None:
    final = run_pipeline(
        log_path=sample_project["log"],
        egp_path=sample_project["egp"],
        parse_only=True,
    )
    assert len(final["errors"]) == 1
    assert final["patches"] == []


def test_pipeline_full_run(sample_project: dict[str, str]) -> None:
    final = _run_full(sample_project)
    assert final["output_path"] is not None
    assert Path(final["output_path"]).exists()


def test_pipeline_run_log_ordered(sample_project: dict[str, str]) -> None:
    final = _run_full(sample_project)
    log = final["run_log"]
    node_names = [
        "parse_log",
        "map_errors",
        "diagnose_errors",
        "generate_patches",
        "apply_patches",
    ]
    positions = [
        next((i for i, entry in enumerate(log) if name in entry), -1)
        for name in node_names
    ]
    assert positions == sorted(positions), "run_log entries are not in pipeline order"


def test_pipeline_valid_zip_output(sample_project: dict[str, str]) -> None:
    final = _run_full(sample_project)
    assert final["output_path"] is not None
    assert zipfile.is_zipfile(final["output_path"])


def test_pipeline_output_contains_fix(sample_project: dict[str, str]) -> None:
    final = _run_full(sample_project)
    assert final["output_path"] is not None
    with zipfile.ZipFile(final["output_path"], "r") as zf:
        xml_bytes = zf.read("project.xml")
    assert b"salary * 1.1" in xml_bytes
    assert b"salry * 1.1" not in xml_bytes


def test_pipeline_original_untouched(sample_project: dict[str, str]) -> None:
    egp_path = sample_project["egp"]
    original_bytes = Path(egp_path).read_bytes()
    _run_full(sample_project)
    assert Path(egp_path).read_bytes() == original_bytes
