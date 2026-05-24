"""Streamlit web app — SAS EGP Fixer UI."""

import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import streamlit as st

from agents.orchestrator import run_pipeline
from agents.state import FixerState
from core.log_parser import SASError
from core.report import generate_report

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SAS EGP Fixer",
    page_icon="🔧",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

_STATE_DEFAULTS: dict[str, Any] = {
    "ran": False,
    "result": None,
    "report": "",
    "fixed_egp_bytes": None,
    "fixed_egp_name": None,
    "pipeline_error": None,
}
for _key, _default in _STATE_DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_success_entry(entry: str) -> bool:
    """Return True when a run_log entry deserves a green success widget."""
    if "passed" in entry:
        return True
    numbers = re.findall(r"\d+", entry)
    return any(int(n) > 0 for n in numbers)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("How it works")
    st.markdown(
        "1. Upload your `.log` and `.egp` files\n"
        "2. Click **Fix My SAS Project**\n"
        "3. Pipeline parses errors and maps them to code nodes\n"
        "4. GPT-4o diagnoses each error and generates a fix\n"
        "5. Download your fixed `.egp` and diff report"
    )
    st.divider()
    st.header("Limitations")
    st.markdown(
        "- Macro-generated errors are detected but **not auto-fixed**\n"
        "- LIBNAME / physical file errors are **not auto-fixed** "
        "(external dependency)\n"
        "- Always review `_fixed.egp` before running in production SAS"
    )

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🔧 SAS EGP Fixer")
st.caption("Upload a SAS log file and EGP project — get a fixed EGP back.")
st.divider()

# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

col_log, col_egp = st.columns(2)
with col_log:
    uploaded_log = st.file_uploader("SAS Log File (.log)", type=["log", "txt"])
with col_egp:
    uploaded_egp = st.file_uploader("EGP Project File (.egp)", type=["egp"])

# ---------------------------------------------------------------------------
# Run button
# ---------------------------------------------------------------------------

files_ready: bool = uploaded_log is not None and uploaded_egp is not None
run_clicked: bool = st.button(
    "🚀 Fix My SAS Project",
    type="primary",
    use_container_width=True,
    disabled=not files_ready,
)

# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

if run_clicked and uploaded_log is not None and uploaded_egp is not None:
    tmp_dir = tempfile.mkdtemp()
    try:
        log_path = Path(tmp_dir) / uploaded_log.name
        egp_path = Path(tmp_dir) / uploaded_egp.name
        log_path.write_bytes(uploaded_log.read())
        egp_path.write_bytes(uploaded_egp.read())

        # Validate the EGP is a real ZIP before handing it to the pipeline
        try:
            with zipfile.ZipFile(egp_path):
                pass
        except zipfile.BadZipFile:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            st.error(
                "The uploaded .egp file is not a valid SAS Enterprise Guide project "
                "archive. A real .egp file is a ZIP archive — please export it "
                "directly from SAS Enterprise Guide (File → Save As)."
            )
            st.stop()

        with st.status("Running pipeline...", expanded=True) as status:
            st.write("📂 Parsing log file...")
            final_state: FixerState = run_pipeline(str(log_path), str(egp_path))
            st.write("✅ Pipeline complete")
            status.update(label="Complete", state="complete")

        report_text: str = generate_report(final_state)

        # Read fixed EGP bytes into memory before temp dir is wiped
        output_path: str | None = final_state["output_path"]
        fixed_bytes: bytes | None = None
        fixed_name: str | None = None
        if output_path is not None and Path(output_path).exists():
            fixed_bytes = Path(output_path).read_bytes()
            fixed_name = Path(output_path).name

        st.session_state["result"] = final_state
        st.session_state["report"] = report_text
        st.session_state["fixed_egp_bytes"] = fixed_bytes
        st.session_state["fixed_egp_name"] = fixed_name
        st.session_state["ran"] = True
        st.session_state["pipeline_error"] = None

    except Exception as exc:
        logger.exception("Pipeline failed")
        st.session_state["ran"] = False
        st.session_state["pipeline_error"] = str(exc)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    st.rerun()

# ---------------------------------------------------------------------------
# Persistent error banner (survives rerun)
# ---------------------------------------------------------------------------

if st.session_state["pipeline_error"]:
    st.error(f"Pipeline failed: {st.session_state['pipeline_error']}")

# ---------------------------------------------------------------------------
# Results section
# ---------------------------------------------------------------------------

if st.session_state["ran"] and st.session_state["result"] is not None:
    state: FixerState = st.session_state["result"]
    report_text_display: str = st.session_state["report"]

    tab_log, tab_errors, tab_report = st.tabs(
        ["📋 Pipeline Log", "🔍 Errors Found", "📄 Diff Report"]
    )

    # --- Tab 1: Pipeline Log ---
    with tab_log:
        run_log: list[str] = state["run_log"]
        if run_log:
            for entry in run_log:
                if _is_success_entry(entry):
                    st.success(entry)
                else:
                    st.info(entry)
        else:
            st.info("No pipeline log entries.")

        errors_encountered: list[str] = state["errors_encountered"]
        if errors_encountered:
            st.divider()
            for err_entry in errors_encountered:
                st.error(err_entry)

    # --- Tab 2: Errors Found ---
    with tab_errors:
        errors: list[SASError] = state["errors"]
        if errors:
            for error in errors:
                line_ref = (
                    str(error.log_line_number)
                    if error.log_line_number is not None
                    else "?"
                )
                preview = error.message[:60]
                label = f"[{error.severity}] Line {line_ref} — {preview}..."
                with st.expander(label):
                    if error.severity == "ERROR":
                        st.error(error.message)
                    else:
                        st.warning(error.message)
                    source = error.echoed_source_line or "unavailable"
                    st.caption(f"Source: {source}")
                    if error.is_macro_generated:
                        st.caption("⚠ macro-generated")
        else:
            st.info("No errors found in the log.")

    # --- Tab 3: Diff Report ---
    with tab_report:
        st.code(report_text_display, language="diff")

    # -----------------------------------------------------------------------
    # Download buttons — report is always available; EGP only when patched
    # -----------------------------------------------------------------------

    if st.session_state.get("ran"):
        st.divider()
        st.subheader("Downloads")
        dl_col1, dl_col2 = st.columns(2)

        with dl_col1:
            if st.session_state.get("fixed_egp_bytes"):
                st.download_button(
                    label="⬇️ Download Fixed EGP",
                    data=st.session_state["fixed_egp_bytes"],
                    file_name=st.session_state["fixed_egp_name"],
                    mime="application/zip",
                    use_container_width=True,
                )
            else:
                st.info("No fixed EGP generated — no patches were applied.")

        with dl_col2:
            st.download_button(
                label="⬇️ Download Diff Report",
                data=st.session_state["report"].encode(),
                file_name="sas_fixer_report.txt",
                mime="text/plain",
                use_container_width=True,
            )
