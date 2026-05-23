# SAS EGP Fixer

SAS EGP Fixer is an autonomous LLM pipeline that reads a SAS `.log` file, diagnoses every ERROR and WARNING using OpenAI GPT-4o, generates targeted code fixes, and writes a corrected `.egp` Enterprise Guide project archive — all without touching the original file. It is aimed at SAS developers and data engineers who spend time manually hunting through verbose SAS logs and hand-editing XML-wrapped code nodes inside `.egp` archives; this tool automates that loop end-to-end, from log line to fixed project file.

---

## How It Works

1. **Parse** — reads the `.log` file and extracts structured `SASError` records (severity, message, source line, macro flag) using a two-pass regex engine.
2. **Map** — opens the `.egp` ZIP archive, walks every `<Code>` element, builds a cumulative global line map, and resolves each error's log line number to the exact code node and local line offset.
3. **Diagnose** — sends each mapped error and its surrounding code context to GPT-4o, which classifies the root cause and decides whether the error is auto-fixable.
4. **Patch** — for each fixable error, sends the complete raw code node to GPT-4o and receives a fully rewritten corrected version.
5. **Write** — applies every patch back into a new `_fixed.egp` ZIP archive; the original `.egp` is never modified.

```
.log file → [parse_log] → [map_errors] → [diagnose_errors] → [generate_patches] → [apply_patches] → _fixed.egp
```

---

## Architecture

| Layer | Module | Responsibility |
|---|---|---|
| Core | `core/log_parser.py` | Two-pass SAS log parser → `list[SASError]` |
| Core | `core/egp_mapper.py` | ZIP/XML walker → line offset map + `ErrorMapping` |
| Core | `core/egp_writer.py` | CDATA-safe XML patcher → `_fixed.egp` ZIP |
| Core | `core/report.py` | Unified diff report writer |
| Agents | `agents/llm.py` | Single OpenAI client instantiation point |
| Agents | `agents/diagnosis_agent.py` | LangGraph node: LLM error classification |
| Agents | `agents/patch_agent.py` | LangGraph node: LLM code fix generation |
| Agents | `agents/orchestrator.py` | `StateGraph` wiring + `run_pipeline()` entry point |
| CLI | `main.py` | argparse CLI with three execution modes |
| Config | `config.py` | `python-dotenv` config loader |

All agent nodes communicate exclusively through `FixerState` (`agents/state.py`), a `TypedDict` that is the single state contract for the LangGraph pipeline. The OpenAI client is instantiated in exactly one place (`agents/llm.py`) and injected from there.

---

## Requirements

- Python 3.13
- OpenAI API key (`gpt-4o`)
- No admin rights required — runs entirely inside a `venv`
- No Docker

---

## Setup

1. **Clone the repo**

   ```bash
   git clone <repo-url>
   cd "SAS Log Detection and Auto fix"
   ```

2. **Create and activate a virtual environment**

   Windows:
   ```bat
   python -m venv venv
   venv\Scripts\activate
   ```

   macOS / Linux:
   ```bash
   python3.13 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your API key**

   ```bash
   cp .env.example .env
   # Open .env and set OPENAI_API_KEY=sk-...
   ```

5. **Generate the sample EGP project file**

   ```bash
   python sample_data/create_sample_egp.py
   # Created: sample_data/sample_project.egp
   ```

---

## Usage

### Parse only (no LLM)

Extracts and pretty-prints all errors from the log, then exits. No API calls made.

```bash
python main.py --log your.log --egp your_project.egp --parse-only
```

```
Parsed log: 5 error(s), 1 warning(s)

[ERROR] line 4
  Message: Variable SALRY not found in data set MYLIB.STAFF.
  Source:  salary_adj = salry * 1.1;
...
```

### Dry run (no LLM)

Parses the log, maps every error to its code node, and prints the EGP line map. No API calls made.

```bash
python main.py --log your.log --egp your_project.egp --dry-run
```

```
parse_log: found 5 error(s), 1 warning(s)
map_errors: mapped 4 of 5 error(s) to code nodes
diagnose_errors: skipped (dry_run/parse_only)
...
EGP line map: your_project.egp
  [   1-1   ]  Setup Library  (project.xml)
  [   2-6   ]  Load Employees  (project.xml)
  [   7-12  ]  SQL Summary  (project.xml)
  Total submitted lines: 12
Errors mapped: 4
```

### Full run

Diagnoses all mapped errors with GPT-4o, generates code fixes, and writes the patched archive.

```bash
python main.py --log your.log --egp your_project.egp
```

```
parse_log: found 5 error(s), 1 warning(s)
map_errors: mapped 4 of 5 error(s) to code nodes
diagnose_errors: diagnosed 4 error(s)
generate_patches: generated 2 patch(es)
apply_patches: wrote 2 patch(es) to your_project_fixed.egp
Fixed EGP written: your_project_fixed.egp
Report written: your_project_fixed_report.txt
```

Outputs:
- **`your_project_fixed.egp`** — patched SAS Enterprise Guide project archive
- **`your_project_fixed_report.txt`** — plain-text report with pipeline log, error list, unified diffs for every patch applied, and a footer summary

---

## Testing

All 88 tests are fully mocked — no OpenAI API key is needed to run the suite.

```bash
pytest -v                            # 88 tests, all pass
mypy . --strict --exclude venv       # 0 type errors
```

---

## Claude Code Slash Commands

These commands are defined in `.claude/commands/` and can be run from the Claude Code CLI.

| Command | What it does |
|---|---|
| `/run-tests` | Run the full `pytest -v` suite and report results |
| `/dry-run` | Parse the sample log and show the EGP error-to-line map; no LLM calls |
| `/parse-only` | Extract and pretty-print errors from the sample log, then stop |
| `/show-map` | Print the EGP code node line-offset table for `sample_data/sample_project.egp` |
| `/lint` | Run `black .` then `ruff check --fix .` on the entire codebase |

---

## Limitations

- Requires the `.log` and `.egp` to come from the same SAS Enterprise Guide run — line numbers must align.
- Macro-generated errors are detected but not auto-fixed; they are marked `fixable=false` in the diagnosis and skipped by the patch agent.
- Physical file / LIBNAME errors (`Physical file does not exist`) are not auto-fixed because they represent external dependencies that must be resolved on the SAS server.
- Tested against SAS 9.4 Enterprise Guide project files; other versions may use different XML schemas or tag names inside the `.egp` archive.
- LLM fixes are best-effort — always review `_fixed.egp` before running it in a production SAS environment.

---

## Project Structure

```
SAS Log Detection and Auto fix
│
├── main.py                  # CLI entry point — argparse, loads config, runs orchestrator
├── config.py                # Reads .env via python-dotenv, exposes typed Config dataclass
├── requirements.txt         # Pinned dependencies
├── .env                     # Local secrets — never committed (gitignored)
├── .env.example             # Shape of .env with placeholder values — always committed
├── CLAUDE.md                # Project constitution and contributor contract
│
├── core/
│   ├── log_parser.py        # Parses SAS .log → list[SASError] dataclass records
│   ├── egp_mapper.py        # Opens .egp (ZIP), finds code nodes, maps errors to line offsets
│   ├── egp_writer.py        # Writes updated code nodes back into a new _fixed.egp ZIP
│   └── report.py            # Generates plain-text diff report after a pipeline run
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
│   ├── test_patch_agent.py
│   ├── test_orchestrator.py
│   └── test_report.py
│
├── sample_data/
│   ├── sample.log           # Representative SAS log with 5 real error patterns
│   ├── sample_project.egp   # Matching SAS EG project archive (3 code nodes)
│   └── create_sample_egp.py # Script to regenerate sample_project.egp
│
├── venv/                    # Virtual environment — never committed (gitignored)
└── .claude/
    └── commands/            # Slash command definitions for Claude Code CLI
```

---

## License

MIT
