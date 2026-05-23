"""Tests for core/egp_writer.py."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from core.egp_writer import _derive_output_path, apply_patches

# ---------------------------------------------------------------------------
# Shared XML content
# ---------------------------------------------------------------------------

EGP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<SASProject xmlns="urn:schemas-sas-com:egproject">
  <Tasks>
    <Task id="task001" name="Clean Salaries">
      <Code id="node001" name="Clean Salaries">
data work.salaries_clean;
    set work.employees;
    salary_new = salry * 1.1;
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
def egp_file(tmp_path: Path) -> str:
    """Write EGP_XML into a real .egp ZIP on disk and return its path as a string."""
    egp_path = tmp_path / "project.egp"
    with zipfile.ZipFile(egp_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", EGP_XML)
    return str(egp_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_code_element(zip_path: str, node_id: str) -> str | None:
    """Open a .egp ZIP, parse project.xml, return the text of the Code element with id=node_id."""
    with zipfile.ZipFile(zip_path) as zf:
        xml_bytes = zf.read("project.xml")
    root = ET.fromstring(xml_bytes)
    for elem in root.iter():
        bare = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if bare == "Code" and elem.get("id") == node_id:
            return elem.text
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_output_path_derived(egp_file: str) -> None:
    output_path, _ = apply_patches(egp_file, [])
    assert output_path.endswith("_fixed.egp")


def test_output_file_exists(egp_file: str) -> None:
    output_path, _ = apply_patches(egp_file, [])
    assert Path(output_path).exists()


def test_output_is_valid_zip(egp_file: str) -> None:
    output_path, _ = apply_patches(egp_file, [])
    with zipfile.ZipFile(output_path) as zf:
        assert len(zf.namelist()) >= 1


def test_patch_applied_to_code(egp_file: str) -> None:
    new_code = (
        "data work.salaries_clean;\n"
        "    set work.employees;\n"
        "    salary_new = salary * 1.1;\n"
        "run;\n"
    )
    patches = [{"node_id": "node001", "xml_file": "project.xml", "new_code": new_code}]
    output_path, _ = apply_patches(egp_file, patches)

    text = _read_code_element(output_path, "node001")
    assert text is not None
    assert "salary * 1.1" in text
    assert "salry" not in text


def test_patch_result_success(egp_file: str) -> None:
    patches = [
        {"node_id": "node001", "xml_file": "project.xml", "new_code": "fixed;\n"}
    ]
    _, results = apply_patches(egp_file, patches)

    matched = [r for r in results if r.node_id == "node001"]
    assert len(matched) == 1
    assert matched[0].success is True
    assert matched[0].error_message is None


def test_unmatched_node_returns_failure(egp_file: str) -> None:
    patches = [{"node_id": "node999", "xml_file": "project.xml", "new_code": "oops;\n"}]
    _, results = apply_patches(egp_file, patches)

    matched = [r for r in results if r.node_id == "node999"]
    assert len(matched) == 1
    assert matched[0].success is False


def test_original_file_untouched(egp_file: str) -> None:
    patches = [
        {
            "node_id": "node001",
            "xml_file": "project.xml",
            "new_code": "salary_new = salary * 1.1;\n",
        }
    ]
    apply_patches(egp_file, patches)

    with zipfile.ZipFile(egp_file) as zf:
        original_bytes = zf.read("project.xml")
    assert b"salry" in original_bytes


def test_two_patches_both_applied(egp_file: str) -> None:
    new_code1 = (
        "data work.salaries_clean;\n"
        "    set work.employees;\n"
        "    salary_new = salary * 1.1;\n"
        "run;\n"
    )
    new_code2 = (
        "proc means data=work.salaries_clean;\n"
        "    var salary_new;\n"
        "    class department;\n"
        "run;\n"
    )
    patches = [
        {"node_id": "node001", "xml_file": "project.xml", "new_code": new_code1},
        {"node_id": "node002", "xml_file": "project.xml", "new_code": new_code2},
    ]
    output_path, results = apply_patches(egp_file, patches)

    assert all(r.success for r in results), [r for r in results if not r.success]

    text1 = _read_code_element(output_path, "node001")
    text2 = _read_code_element(output_path, "node002")
    assert text1 is not None and "salary * 1.1" in text1
    assert text2 is not None and "class department" in text2


def test_xml_declaration_preserved(egp_file: str) -> None:
    output_path, _ = apply_patches(egp_file, [])
    with zipfile.ZipFile(output_path) as zf:
        xml_bytes = zf.read("project.xml")
    assert xml_bytes.lstrip().startswith(b"<?xml")


def test_derive_output_path() -> None:
    assert _derive_output_path("project.egp") == "project_fixed.egp"
    assert Path(_derive_output_path("C:/work/my_analysis.egp")) == Path(
        "C:/work/my_analysis_fixed.egp"
    )


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


def test_multi_file_patch_correct_file(multi_file_egp: str) -> None:
    new_code = (
        "data work.clean;\n"
        "    set work.raw;\n"
        "    salary = salary * 1.1;\n"
        "run;\n"
    )
    patches = [{"node_id": "node002", "xml_file": "step2.xml", "new_code": new_code}]
    output_path, results = apply_patches(multi_file_egp, patches)

    assert all(r.success for r in results), [r for r in results if not r.success]

    with zipfile.ZipFile(output_path) as zf:
        step2_bytes = zf.read("step2.xml")
        step1_bytes = zf.read("step1.xml")

    # step2 has the fix, original typo is gone
    assert b"salary = salary * 1.1" in step2_bytes
    assert b"salry" not in step2_bytes
    # step1 is untouched
    assert b"mylib.source" in step1_bytes


def test_multi_file_original_untouched(multi_file_egp: str) -> None:
    patches = [{"node_id": "node002", "xml_file": "step2.xml", "new_code": "fixed;\n"}]
    apply_patches(multi_file_egp, patches)

    with zipfile.ZipFile(multi_file_egp) as zf:
        step1_bytes = zf.read("step1.xml")
        step2_bytes = zf.read("step2.xml")

    assert b"mylib.source" in step1_bytes
    assert b"salry" in step2_bytes
