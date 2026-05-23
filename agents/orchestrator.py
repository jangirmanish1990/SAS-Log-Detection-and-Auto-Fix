"""Builds and compiles the LangGraph StateGraph that wires all fixer nodes together."""

import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from agents.diagnosis_agent import diagnose_errors
from agents.patch_agent import generate_patches
from agents.state import FixerState
from core.egp_mapper import build_line_map, map_errors
from core.egp_writer import apply_patches
from core.log_parser import parse_log

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


def node_parse_log(state: FixerState) -> dict[str, Any]:
    """Read the SAS .log file and extract structured error records."""
    run_log: list[str] = list(state["run_log"])
    try:
        log_text = Path(state["log_path"]).read_text(encoding="utf-8")
        parsed = parse_log(log_text)
        n = len(parsed.errors)
        w = len(parsed.warnings)
        run_log.append(f"parse_log: found {n} error(s), {w} warning(s)")
        logger.info("parse_log: %d error(s), %d warning(s)", n, w)
        return {"errors": parsed.errors, "run_log": run_log}
    except Exception as exc:
        msg = f"parse_log: failed to read or parse log file: {exc}"
        logger.error(msg)
        run_log.append(msg)
        return {
            "errors": [],
            "run_log": run_log,
            "errors_encountered": list(state["errors_encountered"]) + [msg],
        }


def node_map_errors(state: FixerState) -> dict[str, Any]:
    """Open the .egp archive and map each SASError to its code node."""
    run_log: list[str] = list(state["run_log"])
    try:
        line_map = build_line_map(state["egp_path"])
        mappings = map_errors(state["errors"], line_map)
        total = len(state["errors"])
        n = len(mappings)
        run_log.append(f"map_errors: mapped {n} of {total} error(s) to code nodes")
        logger.info("map_errors: %d/%d error(s) mapped", n, total)
        return {"mappings": mappings, "run_log": run_log}
    except Exception as exc:
        msg = f"map_errors: failed to build line map or map errors: {exc}"
        logger.error(msg)
        run_log.append(msg)
        return {
            "mappings": [],
            "run_log": run_log,
            "errors_encountered": list(state["errors_encountered"]) + [msg],
        }


def node_diagnose_errors(state: FixerState) -> dict[str, Any]:
    """Delegate to diagnose_errors, which handles dry_run/parse_only internally."""
    return diagnose_errors(state)


def node_generate_patches(state: FixerState) -> dict[str, Any]:
    """Delegate to generate_patches, which handles dry_run/parse_only internally."""
    return generate_patches(state)


def node_apply_patches(state: FixerState) -> dict[str, Any]:
    """Write the patched code back into a new _fixed.egp file."""
    run_log: list[str] = list(state["run_log"])

    if state["dry_run"] or state["parse_only"]:
        run_log.append("apply_patches: skipped (dry_run/parse_only)")
        return {"run_log": run_log}

    if not state["patches"]:
        run_log.append("apply_patches: no patches to apply")
        return {"output_path": None, "run_log": run_log}

    try:
        output_path_str, patch_results = apply_patches(
            state["egp_path"], state["patches"]
        )
        n = sum(1 for r in patch_results if r.success)
        run_log.append(f"apply_patches: wrote {n} patch(es) to {output_path_str}")
        logger.info("apply_patches: %d patch(es) written to %s", n, output_path_str)
        return {"output_path": output_path_str, "run_log": run_log}
    except Exception as exc:
        msg = f"apply_patches: failed to write output EGP: {exc}"
        logger.error(msg)
        run_log.append(msg)
        return {
            "output_path": None,
            "run_log": run_log,
            "errors_encountered": list(state["errors_encountered"]) + [msg],
        }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph() -> Any:
    """Compile and return the LangGraph pipeline."""
    graph: StateGraph[FixerState] = StateGraph(FixerState)

    graph.add_node("parse_log", node_parse_log)
    graph.add_node("map_errors", node_map_errors)
    graph.add_node("diagnose_errors", node_diagnose_errors)
    graph.add_node("generate_patches", node_generate_patches)
    graph.add_node("apply_patches", node_apply_patches)

    graph.set_entry_point("parse_log")

    graph.add_edge("parse_log", "map_errors")
    graph.add_edge("map_errors", "diagnose_errors")
    graph.add_edge("diagnose_errors", "generate_patches")
    graph.add_edge("generate_patches", "apply_patches")
    graph.add_edge("apply_patches", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


def run_pipeline(
    log_path: str,
    egp_path: str,
    dry_run: bool = False,
    parse_only: bool = False,
) -> FixerState:
    """Build and invoke the full fixer pipeline, returning the final state."""
    initial_state = FixerState(
        log_path=log_path,
        egp_path=egp_path,
        dry_run=dry_run,
        parse_only=parse_only,
        errors=[],
        mappings=[],
        diagnoses=[],
        patches=[],
        output_path=None,
        run_log=[],
        errors_encountered=[],
    )
    compiled = build_graph()
    final_state: FixerState = compiled.invoke(initial_state)
    logger.info(
        "run_pipeline complete — %d step(s) logged, %d error(s) encountered",
        len(final_state.get("run_log", [])),
        len(final_state.get("errors_encountered", [])),
    )
    return final_state
