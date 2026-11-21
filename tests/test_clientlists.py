import io
import zipfile

import pytest

from feestracker import db, reports
from feestracker.clientlists import ListError, Matcher, names_from_text, read_names


@pytest.mark.parametrize("a, b", [
    ("Pinky Shah", "Shah, Pinky"),
    ("Acme Bakery, L.L.C.", "Acme Bakery LLC"),
    ("RIVERA MARIA", "Rivera Maria"),
    ("Konduru, Sasi K.", "Sasi Konduru"),
    ("Rivera Maria", "Rivera Maria and Jose"),        # spouse added
    ("Biner Bryan", "Biner Brian"),                  # small spelling difference
    ("S Sai Sandeep Ananthabhotla", "S Sai Sandeep VS Ananthabhotla"),
])
def test_same_client(a, b):
    assert Matcher([b]).best(a).same


@pytest.mark.parametrize("a, b", [
    ("DD Housing LLC", "DD Housing Northwest LLC"),  # a different company
    ("Vangoor", "Vangoor Group LLC"),
    ("Sundrish Sharma Inc", "Sharma Sundrish and Amandeep"),  # a person's company, not the person
])
def test_only_a_possible_match(a, b):
    m = Matcher([b]).best(a)
    assert not m.same


def test_different_clients():
    m = Matcher(["Rivera Maria", "Chen & Sons Inc"]).best("John Smith")
    assert not m.same and not m.maybe


def test_csv_with_a_name_column():
    data = b"Email,Client Name,Phone\na@x.com,Rivera Maria,1\nb@x.com,Acme Bakery LLC,2\n,,\n"
    assert read_names(data, "list.csv") == ["Rivera Maria", "Acme Bakery LLC"]


def test_first_and_last_name_columns():
    data = b"First Name,Last Name\nMaria,Rivera\nJose,Chen\n"
    assert read_names(data, "list.csv") == ["Maria Rivera", "Jose Chen"]


def test_tax_software_return_list():
    data = (
        b'"Return Name","First Name","Last Name","Spouse First Name","Spouse Last Name","Company Name"\n'
        b'"1001-Sample Fed Only","","","","",""\n'
        b'"RIVERA, MARIA and JOSE","MARIA","RIVERA","JOSE","RIVERA",""\n'
        b'"RIVERA, MARIA and JOSE-ES q2","MARIA","RIVERA","JOSE","RIVERA",""\n'
        b'"Acme Bakery","","","","","ACME BAKERY LLC"\n'
        b'"Chen Wei-ext","","","","",""\n'
    )
    assert read_names(data, "returns.csv") == [
        "Maria Rivera and Jose Rivera", "Acme Bakery Llc", "Chen Wei"]


def test_pasted_names_keep_commas():
    assert names_from_text("Shah, Pinky\n\nRivera Maria\nshah, pinky\n") == ["Shah, Pinky", "Rivera Maria"]


def make_xlsx(rows):
    strings = sorted({c for r in rows for c in r})
    cells = "".join(
        f'<row r="{i + 1}">' + "".join(
            f'<c r="{chr(65 + j)}{i + 1}" t="s"><v>{strings.index(c)}</v></c>' for j, c in enumerate(r))
        + "</row>"
        for i, r in enumerate(rows)
    )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(
            f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{cells}</sheetData></worksheet>")
    return buf.getvalue()


def test_excel_file():
    data = make_xlsx([["Phone", "Name"], ["1", "Rivera Maria"], ["2", "Acme Bakery LLC"]])
    assert read_names(data, "clients.xlsx") == ["Rivera Maria", "Acme Bakery LLC"]


def test_bad_files():
    with pytest.raises(ListError):
        read_names(b"\xd0\xcf\x11\xe0rest", "old.xls")
    with pytest.raises(ListError):
        read_names(b"PKnot really a zip", "broken.xlsx")
    with pytest.raises(ListError):
        read_names(b"\n\n", "empty.csv")


def test_missing_from_list(conn):
    db.save_client_list(conn, "2023", ["Rivera, Maria", "Acme Bakery LLC", "Lopez Ana"])
    db.save_client_list(conn, "2024", ["Maria Rivera", "Chen and Sons Inc", "Lopez Anna"])
    db.save_client_list(conn, "2025", ["Acme Bakery, LLC", "Lopez Ana"])
    result = reports.missing_from_list(conn, ["2023", "2024"], "2025")
    assert result["total"] == 4  # Rivera and Lopez are on both lists
    assert result["found"] == 2  # Acme, and Lopez spelled two ways
    missing = {e["name"]: e for e in result["missing"]}
    assert list(missing) == ["Rivera, Maria", "Chen and Sons Inc"]
    rivera = missing["Rivera, Maria"]
    assert rivera["years"] == ["2023", "2024"] and rivera["taxdome"] == "Rivera Maria"
    assert rivera["owed"] == 75000
    assert missing["Chen and Sons Inc"]["taxdome"] == "Chen & Sons Inc"


def test_unpaid_by_years(conn):
    rows = reports.unpaid_by_years(conn, ["2022", "2025"])
    assert [r["client"] for r in rows] == ["Rivera Maria"]  # Chen's 2025 invoice owes nothing
    assert rows[0]["by_year"] == {"2022": 60000, "2025": 0}
