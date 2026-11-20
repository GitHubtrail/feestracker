from feestracker import db, reports
from feestracker.taxdome import read_invoices

from .conftest import FIXTURE, TODAY


def test_summary(conn):
    s = reports.summary(conn)
    assert s.invoices == 6
    assert s.billed_cents == 351100
    assert s.collected_cents == 205500
    assert s.outstanding_cents == 155600
    assert s.open_invoices == 3
    assert s.clients_owing == 2
    assert s.last_import


def test_by_year(conn):
    rows = {r["year"]: r for r in reports.by_year(conn)}
    assert list(rows) == ["2026", "2025", "2024", "2022"]
    assert (rows["2026"]["billed"], rows["2026"]["collected"], rows["2026"]["outstanding"]) == (
        105600, 10000, 95600,
    )
    assert rows["2022"]["outstanding"] == 60000
    assert rows["2025"]["outstanding"] == 0


def test_who_owes_with_ages(conn):
    owing = reports.who_owes(conn, TODAY)
    assert [c["client"] for c in owing] == ["Acme Bakery, LLC", "Rivera Maria"]
    acme, rivera = owing
    assert acme["owed"] == 80600 and acme["31_90"] == 80600
    assert rivera["owed"] == 75000
    assert rivera["0_30"] == 15000 and rivera["over_1y"] == 60000
    assert rivera["oldest"].startswith("2022-05-10")
    assert reports.age_totals(owing) == {"0_30": 15000, "31_90": 80600, "91_365": 0, "over_1y": 60000}


def test_who_owes_for_one_year(conn):
    owing = reports.who_owes(conn, TODAY, "2022")
    assert [(c["client"], c["owed"]) for c in owing] == [("Rivera Maria", 60000)]


def test_who_paid(conn):
    assert [(r["client"], r["paid"]) for r in reports.who_paid(conn)] == [
        ("Acme Bakery, LLC", 125500),
        ("Chen & Sons Inc", 50000),
        ("Rivera Maria", 30000),
    ]
    assert [(r["client"], r["paid"]) for r in reports.who_paid(conn, "2026")] == [
        ("Chen & Sons Inc", 50000),
        ("Acme Bakery, LLC", 10000),
    ]


def test_client_search_is_literal(conn):
    assert [c["client"] for c in reports.clients(conn, "acme")] == ["Acme Bakery, LLC"]
    assert reports.clients(conn, "%") == []


def test_reimport_updates_instead_of_duplicating(conn):
    counts = db.save_invoices(conn, read_invoices(FIXTURE), FIXTURE.name)
    assert counts == {"invoices": 6, "added": 0, "updated": 6}
    assert reports.summary(conn).invoices == 6
    assert conn.execute("SELECT COUNT(*) FROM line_items").fetchone()[0] == 10


def test_problems_listed(conn):
    assert {p["number"] for p in reports.problems(conn)} == {"1005", "1006"}
