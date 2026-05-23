"""Creates sample_data/sample_project.egp — a minimal SAS EG project archive."""

import zipfile
from pathlib import Path

EGP_XML = """\
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


def create_egp() -> None:
    output_path = Path(__file__).parent / "sample_project.egp"
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", EGP_XML)
    print(f"Created: {output_path}")


if __name__ == "__main__":
    create_egp()
