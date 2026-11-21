from feestracker import db, reports
from feestracker.taxdome import read_invoices

from .conftest import FIXTURE, TODAY


def test_summary(conn):
    s = reports.summary(conn)
    assert s.invoices == 6
    assert s.billed_cents == 351100
    # $2,055.00 was paid, $100.00 of it above an invoice's amount, which is kept apart
    # so the totals add up.
    assert s.collected_cents == 195500
    assert s.overpaid_cents == 10000 and s.overpaid_invoices == 1
    assert s.outstanding_cents == 155600
    assert s.billed_cents == s.collected_cents + s.outstanding_cents + s.written_off_cents
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
    for r in rows.values():
        assert r["billed"] == r["collected"] + r["outstanding"] + r["written_off"]
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


def test_bad_debt_moves_money_out_of_owed(conn):
    assert db.mark_bad_debt(conn, "1003", "client moved away")
    s = reports.summary(conn)
    assert s.outstanding_cents == 95600  # 155600 minus the 60000 written off
    assert (s.written_off_cents, s.written_off_invoices) == (60000, 1)
    assert s.clients_owing == 2  # Rivera still owes on invoice 1004

    years = {r["year"]: r for r in reports.by_year(conn)}
    assert (years["2022"]["outstanding"], years["2022"]["written_off"]) == (0, 60000)

    rivera = {c["client"]: c for c in reports.who_owes(conn, TODAY)}["Rivera Maria"]
    assert rivera["owed"] == 15000 and rivera["over_1y"] == 0
    assert reports.who_owes(conn, TODAY, "2022") == []

    [bad] = reports.bad_debts(conn)
    assert (bad["number"], bad["written_off_cents"], bad["note"]) == ("1003", 60000, "client moved away")


def test_bad_debt_survives_reimport_and_can_be_undone(conn):
    db.mark_bad_debt(conn, "1003")
    db.save_invoices(conn, read_invoices(FIXTURE), FIXTURE.name)
    assert reports.summary(conn).written_off_cents == 60000

    db.unmark_bad_debt(conn, "1003")
    s = reports.summary(conn)
    assert (s.outstanding_cents, s.written_off_cents) == (155600, 0)
    assert reports.bad_debts(conn) == []


def test_bad_debt_needs_a_real_invoice(conn):
    assert not db.mark_bad_debt(conn, "nope")
    assert reports.bad_debts(conn) == []


def test_bad_debt_later_paid_counts_nothing(conn):
    db.mark_bad_debt(conn, "1001")  # already paid
    s = reports.summary(conn)
    assert s.written_off_cents == 0 and s.outstanding_cents == 155600


def test_unpaid_by_years(conn):
    rows = reports.unpaid_by_years(conn, ["2022", "2025"])
    assert [r["client"] for r in rows] == ["Rivera Maria"]  # Chen's 2025 invoice owes nothing
    assert rows[0]["by_year"] == {"2022": 60000, "2025": 0}


def test_totals_add_up_with_bad_debts_and_part_payments(conn):
    db.mark_bad_debt(conn, "1003")
    db.add_payment(conn, "1004", 5000, "2026-11-15")
    db.add_payment(conn, "1002", 999999, "2026-11-15")  # more than is owed
    s = reports.summary(conn)
    assert s.billed_cents == s.collected_cents + s.outstanding_cents + s.written_off_cents
    for r in reports.by_year(conn):
        assert r["billed"] == r["collected"] + r["outstanding"] + r["written_off"]


def test_overpayments_are_explained(tmp_path):
    from decimal import Decimal

    from feestracker.taxdome import Invoice, LineItem, Payment

    def inv(number, client, total, paid):
        return Invoice(number=number, status="paid", client=client, assigned_to="", description="",
                       created_at="", updated_at="", invoice_date="2025-01-01", paid_at="2025-01-02",
                       payment_methods="Credit card", timezone="",
                       line_items=[LineItem("service", 1, "Tax return", total, Decimal(1), "")],
                       payments=[Payment(position=1, creator="", amount_cents=paid, status="succeeded")])

    conn = db.connect(tmp_path / "o.db")
    db.save_invoices(conn, [
        inv("A1", "Card Client", 100000, 103000),       # 3% card fee
        inv("B1", "Shared Client", 30000, 50000),       # one $500 payment for two invoices,
        inv("B2", "Shared Client", 20000, 50000),       # listed on both
        inv("C1", "Odd Client", 10000, 60000),          # something else
    ], "test")
    found = reports.overpayments(conn)
    assert {n: o["kind"] for n, o in found.items()} == {
        "A1": "card_fee", "B1": "shared", "B2": "shared", "C1": "other"}
    totals = reports.overpayment_totals(conn)
    assert totals["card_fee"]["cents"] == 3000 and totals["shared"]["cents"] == 50000
    paid = {r["client"]: r["paid"] for r in reports.who_paid(conn)}
    assert paid["Shared Client"] == 50000  # counted once
    assert paid["Card Client"] == 103000
