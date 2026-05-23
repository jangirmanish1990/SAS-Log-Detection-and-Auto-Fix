# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased] — 2026-05-23

### Added

- **`config.py`** — typed `Config` dataclass loaded from `.env` via `python-dotenv`;
  `configure_logging()` helper sets a consistent timestamp format on the root logger.
- **`main.py`** — full CLI entry point (`argparse`) with three operating modes:
  `--parse-only` (extract and print errors), `--dry-run` (map errors, no LLM calls),
  and full pipeline (diagnose → patch → write `_fixed.egp`). Exits non-zero when
  errors are encountered.
- **`sample_data/sample.log`** — representative SAS 9.4 log containing one typo error
  (`SALRY`), one syntax error (`classs`), and a downstream WARNING; used as the
  canonical test fixture for manual CLI runs.
- **`sample_data/sample_project.egp`** — matching SAS Enterprise Guide project archive
  (ZIP + XML) with two code nodes (`node001`, `node002`) containing the same bugs as
  the sample log.
- **`sample_data/create_sample_egp.py`** — reproducible script to regenerate
  `sample_project.egp` from its embedded XML source.
- **`.claude/commands/`** — five Claude Code slash commands: `/dry-run`, `/parse-only`,
  `/run-tests`, `/show-map`, `/lint`. Each is a self-contained Markdown prompt that
  runs the appropriate CLI command and explains the output.

### Fixed

- **`core/log_parser.py` — line-number mapping bug** (`REECHO_LINE_RE`, extended
  lookahead, proximity guard on backward scan).
  SAS re-echo lines printed after an error use 7+ leading spaces, exceeding
  `ECHO_LINE_RE`'s `\s{0,6}` limit. The lookahead fell through and latched onto the
  first echoed line from the *next* code block, producing wrong line numbers
  (`line 8` / `line 12` instead of `line 4` / `line 11`).
  Fix: added `REECHO_LINE_RE` for 7+-space re-echo lines; extended the pass-2 scan
  to cover `range(block_start, lookahead_end)` (not just `range(j, …)`); added a
  proximity guard on the backward-scan fallback so it only accepts a candidate within
  5 lines of any number seen in the lookahead window.
- **`core/egp_writer.py` — CDATA stripping** (CRITICAL).
  `ET.fromstring` / `ET.tostring` silently converts `<![CDATA[…]]>` to
  entity-escaped plain text, corrupting every node in any XML file that used CDATA —
  not just the patched one.
  Fix: when `b"<![CDATA["` is detected in the raw bytes, the function switches to a
  regex string-replacement path (`_replace_cdata_content`) that rewrites only the
  targeted element's content in-place and wraps the new code in `<![CDATA[…]]>`.
  ET is retained for namespace registration and parse validation only.
- **`core/egp_writer.py` — node ID resolution mismatch** (CRITICAL).
  `egp_mapper` resolves `node_id` from an ancestor element when a code element
  carries no `id` attribute; `egp_writer` only checked the code element's own
  attributes, so these IDs never matched.
  Fix: `_walk_and_patch` now accepts a `parent_map` and walks up to 3 ancestor levels
  when the code element's own attributes produce no match.
- **`core/egp_writer.py` — ZIP metadata loss** (HIGH).
  The output ZIP was rebuilt using only filenames, discarding original timestamps,
  compression type, and external attributes.
  Fix: `apply_patches` now loads members via `zf.infolist()` (preserving `ZipInfo`)
  and writes the output with `out_zf.writestr(zip_info, data)`.
- **`core/egp_writer.py` — empty `node_id` matching wrong elements** (HIGH).
  A patch with an empty or whitespace `node_id` could match unintended elements.
  Fix: such patches are filtered out before `patch_lookup` is built; each produces an
  explicit `PatchResult(success=False)` and a `logger.warning`.
- **`core/egp_writer.py` — duplicate `node_id` silently dropped** (HIGH).
  When the same `node_id` appeared more than once in the patch list, the earlier
  entries were silently discarded (last wins via dict comprehension).
  Fix: duplicates are now detected and logged explicitly before the lookup is built.

### Notes

- All 72 pytest tests pass after every change in this session (`pytest -v` in 1.2 s).
- `black .` and `ruff check .` are clean; no reformatting was needed.
- The original `.egp` file is never modified; output is always written to
  `<name>_fixed.egp`.
