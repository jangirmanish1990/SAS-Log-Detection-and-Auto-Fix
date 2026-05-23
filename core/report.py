"""Generates a plain-text diff report after a pipeline run."""

import difflib
import logging
from datetime import datetime
from pathlib import Path

from agents.state import FixerState
from core.egp_mapper import ErrorMapping

logger = logging.getLogger(__name__)

_HEAVY = "═" * 55
_LIGHT = "─" * 55


def generate_report(state: FixerState) -> str:
    """Build a plain-text report summarising what the pipeline did.
    Returns the report as a string. Does not write to disk.
    """
    errors = state["errors"]
    patches = state["patches"]
    errors_encountered = state["errors_encountered"]
    run_log = state["run_log"]

    mapping_by_node: dict[str, ErrorMapping] = {
        m.node_id: m for m in state["mappings"]
    }

    parts: list[str] = []

    # 1. Header
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_display = state["output_path"] or "None (dry run or no patches)"
    parts += [
        _HEAVY,
        "SAS EGP FIXER — RUN REPORT",
        now,
        _HEAVY,
        f"Log file : {state['log_path']}",
        f"EGP file : {state['egp_path']}",
        f"Output   : {output_display}",
        "",
    ]

    # 2. Pipeline log
    parts += [_LIGHT, "PIPELINE LOG", _LIGHT]
    for entry in run_log:
        parts.append(f"  ▸ {entry}")
    parts.append("")

    # 3. Errors found
    parts += [_LIGHT, f"ERRORS FOUND ({len(errors)})", _LIGHT]
    for error in errors:
        line_ref = str(error.log_line_number) if error.log_line_number is not None else "?"
        parts.append(f"[{error.severity}] Line {line_ref}  {error.message}")
        source = error.echoed_source_line or "unavailable"
        parts.append(f"  Source: {source}")
        if error.is_macro_generated:
            parts.append("  ⚠ macro-generated")
    parts.append("")

    # 4. Patches applied (skip if none)
    if patches:
        parts += [_LIGHT, f"PATCHES APPLIED ({len(patches)})", _LIGHT]
        for patch in patches:
            node_id: str = patch.get("node_id", "")
            xml_file: str = patch.get("xml_file", "")
            explanation: str = patch.get("explanation", "")
            new_code: str = patch.get("new_code", "")

            parts += [
                f"Node : {node_id}",
                f"File : {xml_file}",
                f"What : {explanation}",
                "",
            ]

            mapping = mapping_by_node.get(node_id)
            if mapping is None:
                parts.append("  (original not available)")
            else:
                original_lines = mapping.node_code.splitlines()
                new_lines = new_code.splitlines()
                diff = list(
                    difflib.unified_diff(
                        original_lines,
                        new_lines,
                        fromfile="original",
                        tofile="fixed",
                        lineterm="",
                    )
                )
                if diff:
                    for diff_line in diff:
                        parts.append(f"  {diff_line}")
                else:
                    parts.append("  (no textual change)")
            parts.append("")

    # 5. Issues encountered (skip if none)
    if errors_encountered:
        parts += [_LIGHT, f"ISSUES ENCOUNTERED ({len(errors_encountered)})", _LIGHT]
        for entry in errors_encountered:
            parts.append(f"✗ {entry}")
        parts.append("")

    # 6. Footer
    footer_line = (
        f"{len(patches)} patch(es) applied  ·  "
        f"{len(errors_encountered)} issue(s)  ·  "
        f"{len(errors)} error(s) found in log"
    )
    parts += [_HEAVY, footer_line, _HEAVY]

    return "\n".join(parts)


def write_report(state: FixerState, report_path: str | None = None) -> str:
    """Generate the report and write it to disk.

    If report_path is None, derive it from output_path:
      sample_project_fixed.egp → sample_project_fixed_report.txt
    If output_path is also None, write to sas_egp_fixer_report.txt
    in the current directory.
    Returns the path where the report was written.
    """
    if report_path is None:
        output_path = state["output_path"]
        if output_path:
            p = Path(output_path)
            report_path = str(p.with_name(p.stem + "_report.txt"))
        else:
            report_path = "sas_egp_fixer_report.txt"

    report_text = generate_report(state)
    Path(report_path).write_text(report_text, encoding="utf-8")
    logger.info("Report written to %s", report_path)
    return report_path
