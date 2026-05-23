"""Creates sample_data/sample_project.egp — a minimal SAS EG project archive."""

import zipfile
from pathlib import Path

# No leading newline inside any <Code> element so that splitlines() gives exact
# line counts: node001 = 1 line (global 1), node002 = 5 lines (global 2-6),
# node003 = 6 lines (global 7-12).  This aligns with SAS log line references
# 4 (SALRY), 5 (HIRE_DATE), and 11 (gruop) in sample.log.
EGP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<SASProject xmlns="urn:schemas-sas-com:egproject">
  <Tasks>
    <Task id="task001" name="Setup Library">
      <Code id="node001" name="Setup Library">libname mylib 'C:\\data\\hr';
</Code>
    </Task>
    <Task id="task002" name="Load Employees">
      <Code id="node002" name="Load Employees">data work.employees;
    set mylib.staff;
    salary_adj = salry * 1.1;
    hire_dte = input(hire_date, date9.);
run;
</Code>
    </Task>
    <Task id="task003" name="SQL Summary">
      <Code id="node003" name="SQL Summary">proc sql;
    create table work.summary as
    select dept, avg(salary_adj) as avg_sal
    from work.employees
    gruop by dept;
quit;
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
