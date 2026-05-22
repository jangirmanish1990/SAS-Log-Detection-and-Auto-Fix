# SAS EGP Fixer — Project Constitution

> Single source of truth for how Claude Code and human engineers work in this repo.
> Every section is a contract, not a suggestion.

---
## Project Description

SAS EGP Fixer — an autonomous LLM pipeline
that parses SAS logs, diagnoses errors, and applies fixes back into Enterprise Guide project
files.


---

## 1. What We Are Building

**SAS EGP Fixer** is an autonomous agentic pipeline that:

1. Reads a SAS `.log` file and extracts structured error records
2. Maps each error to the exact code node (line offset) inside a `.egp` file (SAS Enterprise Guide project archive)
3. Calls OpenAI LLM to diagnose each error and generate a targeted fix
4. Writes a corrected `<name>_fixed.egp` file — the original is never touched

### Primary surfaces

| Surface | Entry point |
|---|---|
| CLI | `main.py` |
| Core parsing & I/O | `core/` |
| LangGraph agent pipeline | `agents/` |
| Tests | `tests/` |

---

## 2. Non-Negotiable Rules

These are hard constraints. Violating any of them is a blocking defect, not a style issue.

| # | Rule |
|---|---|
| 1 | **LLM provider is OpenAI only.** Model: `gpt-4o` for reasoning, `gpt-4o-mini` for fast/cheap calls. All `openai.OpenAI()` clients are instantiated **only** in `agents/llm.py` — nowhere else in the codebase. |
| 2 | **Never overwrite the original `.egp`.** Output is always written to `<original_name>_fixed.egp`. |
| 3 | **All agent nodes return `TypedDict` state.** No raw `dict` values passed between LangGraph nodes. The canonical state type is `FixerState` from `agents/state.py`. |
| 4 | **Errors are never silently swallowed.** Every `except` block must either log and re-raise, or propagate a structured error into `FixerState`. No bare `pass`, no silent `except Exception`. |
| 5 | **Python 3.13.** No f-strings with backslashes — assign the value to a variable first, then interpolate. |
| 6 | **`python-dotenv` for all environment config.** Load via `.env` file at startup. Never hardcode keys or paths. |
| 7 | **No secrets in the repo.** `.env` is `.gitignore`d. `.env.example` documents the required keys (`OPENAI_API_KEY`, etc.) with placeholder values. |

---

## 3. Repository Layout

```
SAS Log Detection and Auto fix
│
├── main.py                  # CLI entry point — argparse, loads config, runs orchestrator
├── config.py                # Reads .env via python-dotenv, exposes typed Config dataclass
├── requirements.txt         # Pinned dependencies
├── .env                     # Local secrets — never committed (gitignored)
├── .env.example             # Shape of .env with placeholder values — always committed
├── CLAUDE.md                # This file
│
├── core/
│   ├── log_parser.py        # Parses SAS .log → list[SASError] dataclass records
│   ├── egp_mapper.py        # Opens .egp (ZIP), finds code nodes, maps errors to line offsets
│   └── egp_writer.py        # Writes updated code nodes back into a new _fixed.egp ZIP
│
├── agents/
│   ├── state.py             # FixerState TypedDict — single definition, imported everywhere
│   ├── llm.py               # ONLY place OpenAI client is instantiated
│   ├── orchestrator.py      # Builds and compiles the LangGraph StateGraph
│   ├── diagnosis_agent.py   # Node: diagnose_errors — calls LLM to classify each SASError
│   └── patch_agent.py       # Node: generate_patches — calls LLM to produce code fixes
│
├── tests/
│   ├── test_log_parser.py
│   ├── test_egp_mapper.py
│   ├── test_egp_writer.py
│   ├── test_diagnosis_agent.py
│   └── test_patch_agent.py
│
├── sample_data/
│   ├── sample.log           # Representative SAS log with real error patterns
│   └── sample_project.egp   # Matching SAS EG project archive
│
├── venv/                    # Virtual environment — never committed (gitignored)
└── .claude/
    └── commands/            # Slash command definitions (see §7)
```

---

## 4. Tooling Contract

| Tool | Command | When to run |
|---|---|---|
| Formatter | `black .` | Before every commit |
| Linter | `ruff check --fix .` | Before every commit |
| Type checker | `mypy .` | Before every commit |
| Tests | `pytest -q` | Before every commit and after any agent change |
| Package mgmt | `pip install -r requirements.txt` (always run inside the activated venv) | After pulling or adding dependencies |

### CLI flags

```bash
# Dry run — parse log + show line map, no LLM calls made
python main.py --log sample_data/sample.log --egp sample_data/sample_project.egp --dry-run

# Parse only — extract and print errors, then stop
python main.py --log sample_data/sample.log --egp sample_data/sample_project.egp --parse-only

# Full pipeline — diagnose, patch, write _fixed.egp
python main.py --log sample_data/sample.log --egp sample_data/sample_project.egp
```

---

## 5. LangGraph Agent Shape

The pipeline is a single compiled `StateGraph` defined in `agents/orchestrator.py`.

### Node execution order

```
parse_log → map_errors → diagnose_errors → generate_patches → apply_patches
```

### Contracts

- **`FixerState`** (in `agents/state.py`) is the only state type. Every node takes `FixerState` and returns a `dict` with a subset of its keys (LangGraph partial-update pattern).
- **LLM access**: `diagnosis_agent.py` and `patch_agent.py` import `get_llm_client()` from `agents/llm.py`. No node creates its own `openai.OpenAI()` instance.
- **`parse_log`** and **`map_errors`** are pure functions — no LLM calls, safe to run in `--dry-run` mode.
- **`diagnose_errors`** and **`generate_patches`** make LLM calls — they must not run during `--dry-run` or `--parse-only`.
- **`apply_patches`** calls `core/egp_writer.py` — writes `_fixed.egp`, never the original.

### State skeleton (`agents/state.py`)

```python
from typing import TypedDict
from core.log_parser import SASError
from core.egp_mapper import ErrorMapping

class FixerState(TypedDict):
    log_path: str
    egp_path: str
    dry_run: bool
    parse_only: bool
    errors: list[SASError]
    mappings: list[ErrorMapping]
    diagnoses: list[dict]
    patches: list[dict]
    output_path: str
```

> **Note:** `SASError` and `ErrorMapping` are dataclasses defined in `core/log_parser.py` and `core/egp_mapper.py` respectively. Their definitions drive the state shape — define those modules before wiring the graph.

---

## 6. Definition of Done

A change is complete when **all** of the following are true:

- [ ] `black .` exits clean (no reformatting needed)
- [ ] `ruff check .` exits with zero warnings
- [ ] `mypy .` exits with zero errors
- [ ] `pytest -q` passes with no failures
- [ ] No secrets or `.env` values appear in any committed file
- [ ] The original `.egp` file is unmodified after a full run
- [ ] The `_fixed.egp` output is a valid ZIP archive containing well-formed XML nodes
- [ ] All new agent nodes return `FixerState`-compatible partial dicts
- [ ] No `openai.OpenAI()` client instantiation outside `agents/llm.py`

---

## 7. Slash Commands

Defined as files in `.claude/commands/`. Run them from the Claude Code CLI.

| Command | What it does |
|---|---|
| `/dry-run` | Parse log + show error-to-line map. No LLM calls. |
| `/parse-only` | Extract and pretty-print errors from the log file, then stop. |
| `/run-tests` | Run the full `pytest -q` suite. |
| `/show-map` | Print the EGP code node line-offset table for `sample_data/sample_project.egp`. |
| `/lint` | Run `black .` then `ruff check --fix .` on the entire codebase. |

---

## 8. What Claude Code Must NOT Do Automatically

- **Do not delete or overwrite the original `.egp` file** — ever, under any circumstance.
- **Do not commit or push to git** without being explicitly asked.
- **Do not instantiate `openai.OpenAI()`** outside of `agents/llm.py`.
- **Do not invent SAS variable names, dataset names, macro names, or library refs** when generating patches — only fix what is clearly wrong based on the error message text and the surrounding code context already present in the `.egp` node.
- **Do not make LLM calls** when `--dry-run` or `--parse-only` flags are active.
- **Do not add dependencies** to `requirements.txt` without noting the reason in the commit message.
- **Do not silently catch exceptions** — if you write an `except` block, it must log the error and either re-raise or set `FixerState` error fields.
