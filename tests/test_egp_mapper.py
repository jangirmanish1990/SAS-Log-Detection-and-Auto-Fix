"""Tests for core/egp_mapper.py."""

import io
import zipfile
from pathlib import Path

import pytest

from core.egp_mapper import EGPLineMap, build_line_map, map_errors
from core.log_parser import SASError

# ---------------------------------------------------------------------------
# Minimal fake .egp content
# ---------------------------------------------------------------------------

# First node:  7 lines via splitlines() (leading \n + 5 code lines + "run;")
# Second node: 5 lines via splitlines() (leading \n + 3 code lines + "run;")
# Cumulative:  node1 [1-7], node2 [8-12], total = 12
_PROJECT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<SASProject xmlns="urn:schemas-sas-com:egproject">
  <Tasks>
    <Task id="task001" name="Clean Salaries">
      <Code id="node001" name="Clean Salaries">
data work.salaries_clean;
    set work.employees;
    salary_new = salry * 1.1;
    if age > 65 then retirement_flag = 1;
    else retirement_flag = 0;
run;
</Code>
    </Task>
    <Task id="task002" name="Summary Report">
      <Code id="node002" name="Summary Report">
proc means data=work.salaries_clean;
    var salary_new;
    classs department;
run;
</Code>
    </Task>
  </Tasks>
</SASProject>
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def egp_bytes() -> io.BytesIO:
    """Return a BytesIO containing a minimal valid .egp ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", _PROJECT_XML)
    buf.seek(0)
    return buf


@pytest.fixture()
def line_map(egp_bytes: io.BytesIO, tmp_path: Path) -> EGPLineMap:
    """Write the in-memory .egp to a temp file and return the built EGPLineMap."""
    egp_file = tmp_path / "sample.egp"
    egp_file.write_bytes(egp_bytes.read())
    return build_line_map(str(egp_file))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_two_nodes_found(line_map: EGPLineMap) -> None:
    assert len(line_map.nodes) == 2


def test_node_names(line_map: EGPLineMap) -> None:
    names = [n.node_name for n in line_map.nodes]
    assert names == ["Clean Salaries", "Summary Report"]


def test_first_node_start_line(line_map: EGPLineMap) -> None:
    assert line_map.nodes[0].start_line == 1


def test_first_node_end_line(line_map: EGPLineMap) -> None:
    # Code text has 7 lines via splitlines(): empty lead + 5 body lines + "run;"
    assert line_map.nodes[0].end_line == 7


def test_second_node_start_line(line_map: EGPLineMap) -> None:
    assert line_map.nodes[1].start_line == 8


def test_total_lines(line_map: EGPLineMap) -> None:
    assert line_map.total_lines >= 12


def test_find_node_first(line_map: EGPLineMap) -> None:
    node, local_line = line_map.find_node(4)
    assert node is not None
    assert node.node_name == "Clean Salaries"
    assert local_line == 4


def test_find_node_second(line_map: EGPLineMap) -> None:
    node, _ = line_map.find_node(10)
    assert node is not None
    assert node.node_name == "Summary Report"


def test_find_node_out_of_range(line_map: EGPLineMap) -> None:
    node, local_line = line_map.find_node(999)
    assert node is None
    assert local_line == -1


def test_get_context_marker(line_map: EGPLineMap) -> None:
    # Local line 4 of node 0 is "    salary_new = salry * 1.1;"
    context = line_map.nodes[0].get_context(4)
    assert ">>>" in context
    assert "salry" in context


def test_summary_output(line_map: EGPLineMap) -> None:
    summary = line_map.summary()
    assert "Clean Salaries" in summary
    assert "Summary Report" in summary


def test_map_errors_returns_mappings(line_map: EGPLineMap) -> None:
    e1 = SASError(
        severity="ERROR",
        message="Variable SALRY not found.",
        log_line_number=4,
        echoed_source_line="salary_new = salry * 1.1;",
        is_macro_generated=False,
        raw_block="ERROR: Variable SALRY not found.",
    )
    e2 = SASError(
        severity="ERROR",
        message="Statement is not valid.",
        log_line_number=10,
        echoed_source_line="classs department;",
        is_macro_generated=False,
        raw_block="ERROR: Statement is not valid.",
    )
    result = map_errors([e1, e2], line_map)
    assert len(result) == 2
    assert result[0].local_line == 4


# ---------------------------------------------------------------------------
# Multi-file EGP fixtures and tests
# ---------------------------------------------------------------------------

_STEP1_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<SASProject xmlns="urn:schemas-sas-com:egproject">
  <Tasks>
    <Task id="task001" name="Load Data">
      <Code id="node001" name="Load Data">
libname mylib 'C:\\data';
data work.raw;
    set mylib.source;
run;
</Code>
    </Task>
  </Tasks>
</SASProject>
"""

_STEP2_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<SASProject xmlns="urn:schemas-sas-com:egproject">
  <Tasks>
    <Task id="task002" name="Transform Data">
      <Code id="node002" name="Transform Data">
data work.clean;
    set work.raw;
    salary = salry * 1.1;
run;
</Code>
    </Task>
  </Tasks>
</SASProject>
"""


@pytest.fixture()
def multi_file_egp(tmp_path: Path) -> str:
    """Write a two-XML-file .egp archive and return its path."""
    egp_path = tmp_path / "multi.egp"
    with zipfile.ZipFile(egp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("step1.xml", _STEP1_XML)
        zf.writestr("step2.xml", _STEP2_XML)
    return str(egp_path)


@pytest.fixture()
def multi_file_line_map(multi_file_egp: str) -> EGPLineMap:
    return build_line_map(multi_file_egp)


def test_multi_file_two_nodes_found(multi_file_line_map: EGPLineMap) -> None:
    assert len(multi_file_line_map.nodes) == 2


def test_multi_file_node_xml_files(multi_file_line_map: EGPLineMap) -> None:
    assert multi_file_line_map.nodes[0].xml_file != multi_file_line_map.nodes[1].xml_file


def test_multi_file_line_continuity(multi_file_line_map: EGPLineMap) -> None:
    assert multi_file_line_map.nodes[1].start_line == multi_file_line_map.nodes[0].end_line + 1


def test_multi_file_find_node_crosses_files(multi_file_line_map: EGPLineMap) -> None:
    # Pick a line clearly inside step2.xml's range
    target = multi_file_line_map.nodes[1].start_line + 1
    node, _ = multi_file_line_map.find_node(target)
    assert node is not None
    assert node.xml_file == "step2.xml"
