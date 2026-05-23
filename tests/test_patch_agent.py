"""Tests for agents/patch_agent.py."""

import json
from unittest.mock import MagicMock, patch

from agents.patch_agent import (
    _FALLBACK_PATCH,
    _parse_patch_response,
    generate_patches,
)
from agents.state import FixerState
from core.egp_mapper import ErrorMapping
from core.log_parser import SASError

_PATCH_TARGET = "agents.patch_agent.get_llm_client"

_FIXED_CODE = (
    "data work.salaries_clean;\n"
    "    set work.employees;\n"
    "    salary_new = salary * 1.1;\n"
    "run;\n"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mapping() -> ErrorMapping:
    error = SASError(
        severity="ERROR",
        message="Variable SALRY not found",
        log_line_number=4,
        echoed_source_line="salary_new = salry * 1.1;",
        is_macro_generated=False,
        raw_block="",
    )
    return ErrorMapping(
        error=error,
        node_id="node001",
        node_name="Clean Salaries",
        xml_file="project.xml",
        local_line=4,
        code_context=(
            "   3 | set work.employees;\n"
            ">>> 4 | salary_new = salry * 1.1;\n"
            "   5 | run;"
        ),
        node_code=(
            "\ndata work.salaries_clean;\n"
            "    set work.employees;\n"
            "    salary_new = salry * 1.1;\n"
            "    if age > 65 then retirement_flag = 1;\n"
            "    else retirement_flag = 0;\n"
            "run;\n"
        ),
    )


def make_diagnosis(fixable: bool = True) -> dict:
    return {
        "node_id": "node001",
        "node_name": "Clean Salaries",
        "root_cause": "Typo in variable name",
        "diagnosis": "SALRY should be SALARY",
        "fixable": fixable,
        "fix_hint": "Rename salry to salary",
    }


def base_state(fixable: bool = True) -> FixerState:
    return FixerState(
        log_path="",
        egp_path="",
        dry_run=False,
        parse_only=False,
        errors=[],
        mappings=[make_mapping()],
        diagnoses=[make_diagnosis(fixable=fixable)],
        patches=[],
        output_path=None,
        run_log=[],
        errors_encountered=[],
    )


def mock_llm_response(new_code: str, explanation: str = "Fixed typo") -> MagicMock:
    """Return a mock OpenAI response whose content is a success patch JSON."""
    content = json.dumps(
        {
            "success": True,
            "new_code": new_code,
            "explanation": explanation,
            "error": "",
        }
    )
    response = MagicMock()
    response.choices[0].message.content = content
    return response


def _mock_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# Tests — guard paths
# ---------------------------------------------------------------------------


def test_dry_run_skips() -> None:
    state = base_state()
    state["dry_run"] = True
    result = generate_patches(state)
    assert "patches" not in result
    assert any("skipped" in entry for entry in result["run_log"])


def test_parse_only_skips() -> None:
    state = base_state()
    state["parse_only"] = True
    result = generate_patches(state)
    assert "patches" not in result
    assert any("skipped" in entry for entry in result["run_log"])


def test_empty_diagnoses_returns_empty() -> None:
    state = base_state()
    state["diagnoses"] = []
    result = generate_patches(state)
    assert result["patches"] == []


def test_unfixable_skipped_no_llm_call() -> None:
    mock_client = MagicMock()
    with patch(_PATCH_TARGET, return_value=mock_client):
        result = generate_patches(base_state(fixable=False))
    mock_client.chat.completions.create.assert_not_called()
    assert result["patches"] == []


# ---------------------------------------------------------------------------
# Tests — successful patch
# ---------------------------------------------------------------------------


def test_successful_patch() -> None:
    response = mock_llm_response(_FIXED_CODE)
    with patch(_PATCH_TARGET, return_value=_mock_client(response)):
        result = generate_patches(base_state())
    assert result["patches"][0]["node_id"] == "node001"
    assert result["patches"][0]["xml_file"] == "project.xml"
    assert "salary * 1.1" in result["patches"][0]["new_code"]


def test_patch_explanation_captured() -> None:
    response = mock_llm_response(_FIXED_CODE, explanation="Fixed typo")
    with patch(_PATCH_TARGET, return_value=_mock_client(response)):
        result = generate_patches(base_state())
    assert result["patches"][0]["explanation"] == "Fixed typo"


def test_run_log_updated() -> None:
    response = mock_llm_response(_FIXED_CODE)
    with patch(_PATCH_TARGET, return_value=_mock_client(response)):
        result = generate_patches(base_state())
    assert any("generated 1 patch" in entry for entry in result["run_log"])


# ---------------------------------------------------------------------------
# Tests — failure and fallback paths
# ---------------------------------------------------------------------------


def test_llm_failure_adds_to_errors() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("timeout")
    with patch(_PATCH_TARGET, return_value=client):
        result = generate_patches(base_state())
    assert result["patches"] == []
    assert any("timeout" in e for e in result["errors_encountered"])


def test_llm_success_false_adds_to_errors() -> None:
    content = json.dumps(
        {
            "success": False,
            "new_code": "",
            "explanation": "",
            "error": "Cannot determine fix",
        }
    )
    response = MagicMock()
    response.choices[0].message.content = content
    with patch(_PATCH_TARGET, return_value=_mock_client(response)):
        result = generate_patches(base_state())
    assert result["patches"] == []
    assert any("Cannot determine fix" in e for e in result["errors_encountered"])


def test_malformed_json_falls_back() -> None:
    response = MagicMock()
    response.choices[0].message.content = "not json %%%"
    with patch(_PATCH_TARGET, return_value=_mock_client(response)):
        result = generate_patches(base_state())
    assert result["patches"] == []


def test_no_matching_mapping() -> None:
    state = base_state()
    state["diagnoses"] = [
        {
            **make_diagnosis(),
            "node_id": "node999",
        }
    ]
    result = generate_patches(state)
    assert result["patches"] == []
    assert len(result["errors_encountered"]) >= 1


# ---------------------------------------------------------------------------
# Tests — _parse_patch_response unit tests
# ---------------------------------------------------------------------------


def test_parse_patch_response_strict_json() -> None:
    payload = {"success": True, "new_code": "x", "explanation": "y", "error": ""}
    result = _parse_patch_response(json.dumps(payload))
    assert result["success"] is True


def test_parse_patch_response_fallback() -> None:
    result = _parse_patch_response("%%%unparseable%%%")
    assert result["success"] is False
    assert result["error"] == "LLM response could not be parsed"


def test_fallback_dict_not_mutated() -> None:
    original_keys = set(_FALLBACK_PATCH.keys())
    _parse_patch_response("garbage 1")
    _parse_patch_response("garbage 2")
    assert set(_FALLBACK_PATCH.keys()) == original_keys
