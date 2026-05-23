"""LangGraph node: generate_patches — calls OpenAI to produce targeted SAS code fixes."""

import json
import logging
import re
from typing import Any, cast

from agents.llm import DEFAULT_MODEL, get_llm_client
from agents.state import FixerState
from core.egp_mapper import ErrorMapping

logger = logging.getLogger(__name__)

PATCH_SYSTEM_PROMPT: str = (
    "You are an expert SAS programmer.\n"
    "You will be given a SAS code block with a known error, a diagnosis of the "
    "root cause, and the specific line that needs to be fixed.\n\n"
    "Your task: return the COMPLETE corrected SAS code block with the fix applied.\n\n"
    "Rules:\n"
    "- Return ONLY a JSON object. No markdown, no explanation outside JSON.\n"
    "- The new_code field must contain the ENTIRE corrected code node, not just "
    "the changed lines.\n"
    "- Make the minimal change needed to fix the error. Do not refactor or "
    "rewrite other parts.\n"
    "- Do not invent variable names, dataset names, or library references that "
    "are not already present in the code — only fix what the error and "
    "diagnosis clearly identify.\n"
    "- If the fix cannot be determined with confidence, set success to false.\n\n"
    "Schema:\n"
    "{\n"
    '  "success": true or false,\n'
    '  "new_code": "complete corrected SAS code",\n'
    '  "explanation": "one sentence describing exactly what was changed",\n'
    '  "error": "if success is false, reason why patch could not be generated"\n'
    "}"
)

_JSON_EXTRACT_RE = re.compile(r"\{.*\}", re.DOTALL)

_FALLBACK_PATCH: dict[str, Any] = {
    "success": False,
    "new_code": "",
    "explanation": "",
    "error": "LLM response could not be parsed",
}


def _build_user_prompt(diagnosis: dict[str, Any], full_code: str, error_context: str) -> str:
    local_line = diagnosis.get("local_line", "unknown")
    return (
        f"Node: {diagnosis.get('node_name', 'unknown')}\n"
        f"Error line (local): {local_line}\n\n"
        f"Root cause: {diagnosis.get('root_cause', '')}\n"
        f"Diagnosis: {diagnosis.get('diagnosis', '')}\n"
        f"Fix hint: {diagnosis.get('fix_hint', '')}\n\n"
        f"Full SAS code node (rewrite this completely with the fix applied):\n{full_code}"
        f"\n\nError context for reference (do not return this format):\n{error_context}"
    )


def _parse_patch_response(raw: str) -> dict[str, Any]:
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

    logger.warning("Could not parse patch LLM response as JSON: %r", raw[:200])
    return dict(_FALLBACK_PATCH)


def generate_patches(state: FixerState) -> dict[str, Any]:
    """LangGraph node: call OpenAI to generate a code fix for each fixable diagnosis."""
    run_log: list[str] = list(state["run_log"])

    if state["dry_run"] or state["parse_only"]:
        run_log.append("generate_patches: skipped (dry_run/parse_only)")
        return {"run_log": run_log}

    diagnoses: list[dict[str, Any]] = state["diagnoses"]
    if not diagnoses:
        logger.warning("generate_patches: no diagnoses available")
        run_log.append("generate_patches: no diagnoses to patch")
        return {"patches": [], "run_log": run_log}

    # Build a node_id → ErrorMapping lookup once for O(1) access per diagnosis
    mapping_by_node: dict[str, ErrorMapping] = {m.node_id: m for m in state["mappings"]}

    client = get_llm_client()
    patches: list[dict[str, Any]] = []
    errors_encountered: list[str] = list(state["errors_encountered"])

    for diagnosis in diagnoses:
        node_id: str = diagnosis.get("node_id", "")

        if not diagnosis.get("fixable", False):
            reason = diagnosis.get("fix_hint", "marked not fixable")
            entry = f"generate_patches: skipped node '{node_id}' — {reason}"
            run_log.append(entry)
            logger.debug(entry)
            continue

        mapping = mapping_by_node.get(node_id)
        if mapping is None:
            msg = f"generate_patches: no mapping found for node '{node_id}', skipping"
            logger.warning(msg)
            errors_encountered.append(msg)
            continue

        try:
            response = client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=[
                    {"role": "system", "content": PATCH_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _build_user_prompt(
                            diagnosis, mapping.node_code, mapping.code_context
                        ),
                    },
                ],
            )
            raw_content: str = response.choices[0].message.content or ""
            result = _parse_patch_response(raw_content)
        except Exception as exc:
            msg = f"generate_patches: LLM call failed for node '{node_id}': {exc}"
            logger.error(msg)
            errors_encountered.append(msg)
            continue

        if result.get("success"):
            patches.append(
                {
                    "node_id": node_id,
                    "xml_file": mapping.xml_file,
                    "new_code": result["new_code"],
                    "explanation": result.get("explanation", ""),
                }
            )
            logger.debug(
                "Patch generated for node %r: %s",
                node_id,
                result.get("explanation", "")[:80],
            )
        else:
            msg = (
                f"generate_patches: LLM could not patch node '{node_id}': "
                f"{result.get('error', 'no reason given')}"
            )
            logger.warning(msg)
            errors_encountered.append(msg)

    n = len(patches)
    run_log.append(f"generate_patches: generated {n} patch(es)")
    logger.info("generate_patches: completed with %d patch(es)", n)

    result_state: dict[str, Any] = {"patches": patches, "run_log": run_log}
    if errors_encountered != list(state["errors_encountered"]):
        result_state["errors_encountered"] = errors_encountered
    return result_state
