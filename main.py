"""CLI entry point — parses arguments, loads config, and runs the fixer orchestrator."""

import argparse
import logging
import sys
from pathlib import Path

from agents.orchestrator import run_pipeline
from config import configure_logging
from core.egp_mapper import build_line_map
from core.log_parser import parse_log, summarize

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SAS EGP Fixer — parse SAS logs, diagnose errors, apply fixes.",
    )
    parser.add_argument("--log", required=True, help="Path to the SAS .log file")
    parser.add_argument("--egp", required=True, help="Path to the .egp project archive")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Map errors without making LLM calls or writing output",
    )
    parser.add_argument(
        "--parse-only",
        action="store_true",
        default=False,
        help="Extract and print errors from the log, then exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    args = parser.parse_args()

    configure_logging(args.log_level)

    log_path = Path(args.log)
    egp_path = Path(args.egp)

    if not log_path.exists():
        log_missing = f"Error: log file not found: {log_path}"
        print(log_missing, file=sys.stderr)
        sys.exit(1)

    if not egp_path.exists():
        egp_missing = f"Error: EGP file not found: {egp_path}"
        print(egp_missing, file=sys.stderr)
        sys.exit(1)

    # --parse-only takes priority when both flags are set
    if args.parse_only and args.dry_run:
        logger.warning(
            "Both --parse-only and --dry-run set; --parse-only takes priority"
        )

    parse_only: bool = args.parse_only
    dry_run: bool = args.dry_run and not args.parse_only

    # ------------------------------------------------------------------ #
    # --parse-only: extract errors and summarise, then exit              #
    # ------------------------------------------------------------------ #
    if parse_only:
        log_text = log_path.read_text(encoding="utf-8")
        parsed = parse_log(log_text)
        print(summarize(parsed))
        sys.exit(0)

    # ------------------------------------------------------------------ #
    # --dry-run: map errors, print line map, no LLM calls                #
    # ------------------------------------------------------------------ #
    if dry_run:
        final = run_pipeline(
            log_path=str(log_path),
            egp_path=str(egp_path),
            dry_run=True,
        )
        for entry in final["run_log"]:
            print(entry)
        line_map = build_line_map(str(egp_path))
        print(line_map.summary())
        mapped_count = f"Errors mapped: {len(final['mappings'])}"
        print(mapped_count)
        sys.exit(0)

    # ------------------------------------------------------------------ #
    # Full run: diagnose, patch, write _fixed.egp                        #
    # ------------------------------------------------------------------ #
    final = run_pipeline(
        log_path=str(log_path),
        egp_path=str(egp_path),
        dry_run=False,
        parse_only=False,
    )

    for entry in final["run_log"]:
        print(entry)

    output_path: str | None = final["output_path"]
    if output_path:
        fixed_msg = f"Fixed EGP written: {output_path}"
        print(fixed_msg)

    errors_encountered: list[str] = final["errors_encountered"]
    for err in errors_encountered:
        logger.warning(err)

    sys.exit(1 if errors_encountered else 0)


if __name__ == "__main__":
    main()
