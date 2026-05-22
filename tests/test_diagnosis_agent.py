"""Tests for agents/diagnosis_agent.py."""

import json
from unittest.mock import MagicMock, patch

from agents.diagnosis_agent import (
    _FALLBACK_DIAGNOSIS,
    _parse_llm_response,
    diagnose_errors,
)
from agents.state import FixerState
from core.egp_mapper import ErrorMapping
from core.log_parser import SASError

_PATCH_TARGET = "agents.diagnosis_agent.get_llm_client"

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
    )


def base_state() -> FixerState:
    return FixerState(
        log_path="",
        egp_path="",
        dry_run=False,
        parse_only=False,
        errors=[],
        mappings=[make_mapping()],
        diagnoses=[],
        patches=[],
        output_path=None,
        run_log=[],
        errors_encountered=[],
    )


def _mock_client(content: str) -> MagicMock:
    """Return a mock OpenAI client whose chat.completions.create returns content."""
    response = MagicMock()
    response.choices[0].message.content = content
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


_GOOD_RESPONSE: dict = {
    "root_cause": "Typo in variable name",
    "diagnosis": "SALRY should be SALARY",
    "fixable": True,
    "fix_hint": "Rename salry to salary",
}

# ---------------------------------------------------------------------------
# Tests — guard paths (no LLM calls)
# ---------------------------------------------------------------------------


def test_dry_run_skips_llm() -> None:
    state = base_state()
    state["dry_run"] = True
    result = diagnose_errors(state)
    assert "diagnoses" not in result
    assert any("skipped" in entry for entry in result["run_log"])


def test_parse_only_skips_llm() -> None:
    state = base_state()
    state["parse_only"] = True
    result = diagnose_errors(state)
    assert "diagnoses" not in result
    assert any("skipped" in entry for entry in result["run_log"])


def test_empty_mappings_returns_empty_diagnoses() -> None:
    state = base_state()
    state["mappings"] = []
    result = diagnose_errors(state)
    assert result["diagnoses"] == []


# ---------------------------------------------------------------------------
# Tests — successful LLM call
# ---------------------------------------------------------------------------


def test_successful_diagnosis() -> None:
    with patch(_PATCH_TARGET, return_value=_mock_client(json.dumps(_GOOD_RESPONSE))):
        result = diagnose_errors(base_state())
    assert result["diagnoses"][0]["fixable"] is True
    assert result["diagnoses"][0]["node_id"] == "node001"
    assert result["diagnoses"][0]["root_cause"] == "Typo in variable name"


def test_diagnosis_adds_node_traceability() -> None:
    with patch(_PATCH_TARGET, return_value=_mock_client(json.dumps(_GOOD_RESPONSE))):
        result = diagnose_errors(base_state())
    diag = result["diagnoses"][0]
    assert "node_id" in diag
    assert "node_name" in diag


def test_run_log_updated() -> None:
    with patch(_PATCH_TARGET, return_value=_mock_client(json.dumps(_GOOD_RESPONSE))):
        result = diagnose_errors(base_state())
    assert any("diagnosed 1 error" in entry for entry in result["run_log"])


# ---------------------------------------------------------------------------
# Tests — failure and fallback paths
# ---------------------------------------------------------------------------


def test_llm_failure_falls_back() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("network error")
    with patch(_PATCH_TARGET, return_value=client):
        result = diagnose_errors(base_state())
    assert result["diagnoses"][0]["fixable"] is False
    assert len(result["errors_encountered"]) == 1
    assert "network error" in result["errors_encountered"][0]


def test_malformed_json_falls_back() -> None:
    with patch(_PATCH_TARGET, return_value=_mock_client("not valid json {{")):
        result = diagnose_errors(base_state())
    assert result["diagnoses"][0]["root_cause"] == "LLM response could not be parsed"


def test_json_in_markdown_extracted() -> None:
    content = (
        "Here is the result:\n"
        "```json\n"
        '{"root_cause": "typo", "diagnosis": "x", "fixable": true, "fix_hint": "y"}\n'
        "```"
    )
    with patch(_PATCH_TARGET, return_value=_mock_client(content)):
        result = diagnose_errors(base_state())
    assert result["diagnoses"][0]["fixable"] is True


# ---------------------------------------------------------------------------
# Tests — _parse_llm_response unit tests
# ---------------------------------------------------------------------------


def test_parse_llm_response_strict_json() -> None:
    payload = {"root_cause": "typo", "fixable": True}
    result = _parse_llm_response(json.dumps(payload))
    assert result["root_cause"] == "typo"
    assert result["fixable"] is True


def test_parse_llm_response_fallback() -> None:
    result = _parse_llm_response("completely unparseable %%%")
    assert result["root_cause"] == "LLM response could not be parsed"


def test_fallback_dict_not_mutated() -> None:
    original_keys = set(_FALLBACK_DIAGNOSIS.keys())
    _parse_llm_response("garbage 1")
    _parse_llm_response("garbage 2")
    assert set(_FALLBACK_DIAGNOSIS.keys()) == original_keys
