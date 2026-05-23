"""Defines FixerState — the single TypedDict shared across all LangGraph nodes."""

from typing import Any, TypedDict

from core.egp_mapper import ErrorMapping
from core.log_parser import SASError


class FixerState(TypedDict):
    log_path: str
    egp_path: str
    dry_run: bool
    parse_only: bool
    errors: list[SASError]
    mappings: list[ErrorMapping]
    diagnoses: list[dict[str, Any]]
    patches: list[dict[str, Any]]
    output_path: str | None
    run_log: list[str]  # human-readable record of what each node did
    errors_encountered: list[str]  # error messages from any failed steps
