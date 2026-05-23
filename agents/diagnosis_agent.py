"""LangGraph node: diagnose_errors — calls OpenAI to classify each SASError."""

import json
import logging
import re
from typing import Any, cast

from agents.llm import DEFAULT_MODEL, get_llm_client
from agents.state import FixerState
from core.egp_mapper import ErrorMapping

logger = logging.getLogger(__name__)

DIAGNOSIS_SYSTEM_PROMPT: str = (
    "You are an expert SAS programmer and debugger.\n"
    "You will be given a SAS error message and the surrounding code context.\n"
    "Diagnose the root cause and determine whether the fix can be applied "
    "automatically to the code.\n\n"
    "Respond ONLY with a valid JSON object. No markdown, no explanation outside JSON.\n\n"
    "Schema:\n"
    "{\n"
    '  "root_cause": "one-sentence root cause",\n'
    '  "diagnosis": "detailed explanation of what went wrong",\n'
    '  "fixable": true or false,\n'
    '  "fix_hint": "what needs to change, or why it cannot be auto-fixed"\n'
    "}\n\n"
    "Set fixable=false only if:\n"
    "- The error requires a missing dataset or library that must be created outside the code\n"
    "- The error is caused by a macro that cannot be located\n"
    "- The fix requires information not present in the code context\n\n"
    "Common SAS error patterns and how to classify them:\n"
    "- 'Physical file does not exist' → fixable=false (external dependency, path must "
    "exist on the SAS server)\n"
    "- 'Variable X not found in data set Y' → fixable=true (usually a typo in the "
    "variable name — check surrounding code for the correct spelling)\n"
    "- 'The following columns were not found' in PROC SQL → fixable=true (usually a "
    "typo in a GROUP BY, ORDER BY, or SELECT clause)\n"
    "- 'Variable X not found' with macro-generated note → fixable=false (macro variable "
    "resolved to wrong value — requires macro parameter fix, not code patch)\n"
    "- File/dataset not found errors → fixable=false (external dependency)"
)

# Extracts the first {...} block from an LLM response that isn't clean JSON
_JSON_EXTRACT_RE = re.compile(r"\{.*\}", re.DOTALL)

_FALLBACK_DIAGNOSIS: dict[str, Any] = {
    "root_cause": "LLM response could not be parsed",
    "diagnosis": "",
    "fixable": False,
    "fix_hint": "Manual review required.",
}


def _build_user_prompt(mapping: ErrorMapping) -> str:
    return (
        f"Error message: {mapping.error.message}\n\n"
        f"Node: {mapping.node_name}\n"
        f"Line in node (local): {mapping.local_line}\n\n"
        f"Code context:\n{mapping.code_context}"
    )


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """Attempt strict JSON parse, then regex extraction, then return fallback."""
    try:
        return cast(dict[str, Any], json.loads(raw))
    except json.JSONDecodeError:
        pass

    match = _JSON_EXTRACT_RE.search(raw)
    if match:
        try:
            return cast(dict[str, Any], json.loads(match.group()))
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse LLM response as JSON: %r", raw[:200])
    return dict(_FALLBACK_DIAGNOSIS)


def diagnose_errors(state: FixerState) -> dict[str, Any]:
    """LangGraph node: call OpenAI to diagnose each mapped SAS error."""
    run_log: list[str] = list(state["run_log"])

    if state["dry_run"] or state["parse_only"]:
        run_log.append("diagnose_errors: skipped (dry_run/parse_only)")
        return {"run_log": run_log}

    mappings: list[ErrorMapping] = state["mappings"]
    if not mappings:
        logger.warning("diagnose_errors: no mappings available to diagnose")
        run_log.append("diagnose_errors: no mappings to diagnose")
        return {"diagnoses": [], "run_log": run_log}

    client = get_llm_client()
    diagnoses: list[dict[str, Any]] = []
    errors_encountered: list[str] = list(state["errors_encountered"])

    for mapping in mappings:
        try:
            response = client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=[
                    {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(mapping)},
                ],
            )
            raw_content: str = response.choices[0].message.content or ""
            diagnosis = _parse_llm_response(raw_content)
        except Exception as exc:
            msg = (
                f"diagnose_errors: LLM call failed for node "
                f"'{mapping.node_id}': {exc}"
            )
            logger.error(msg)
            errors_encountered.append(msg)
            diagnosis = dict(_FALLBACK_DIAGNOSIS)

        diagnosis["node_id"] = mapping.node_id
        diagnosis["node_name"] = mapping.node_name
        diagnoses.append(diagnosis)
        logger.debug(
            "Diagnosed node %r: fixable=%s root_cause=%r",
            mapping.node_id,
            diagnosis.get("fixable"),
            diagnosis.get("root_cause", "")[:80],
        )

    n = len(diagnoses)
    run_log.append(f"diagnose_errors: diagnosed {n} error(s)")
    logger.info("diagnose_errors: completed %d diagnosis(es)", n)

    return {
        "diagnoses": diagnoses,
        "run_log": run_log,
        "errors_encountered": errors_encountered,
    }
