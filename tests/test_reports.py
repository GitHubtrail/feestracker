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


def test_tax_year_written_on_invoice_wins():
    from feestracker.reports import tax_years_of
    assert tax_years_of("Tax year 2023 individual", "2025-03-01") == {"2023"}
    assert tax_years_of("TY2024 return", "2025-03-01") == {"2024"}
    assert tax_years_of("2022 tax return", "2025-03-01") == {"2022"}
    assert tax_years_of("Bookkeeping", "2025-03-01") == {"2024"}


def test_not_billed_for_a_tax_year(conn):
    from feestracker import reports
    db.save_client_list(conn, "2024", ["Acme Bakery LLC", "Maria Rivera", "Chen and Sons", "Nobody Here"])
    nb = reports.not_billed(conn, "2024")
    assert nb["listed"] == 4
    by_name = {r["name"]: r for r in nb["rows"]}
    assert "Chen and Sons" not in by_name  # billed for 2024
    assert by_name["Acme Bakery LLC"]["kind"] == "in_taxdome"
    assert by_name["Maria Rivera"]["kind"] == "in_taxdome"
    assert by_name["Nobody Here"]["kind"] == "never"


def test_obvious_name_variants_match():
    from feestracker.clientlists import Matcher
    m = Matcher(["FareFund LLC", "HyLine Dental Associates, PC", "Chauhan Rohanbhai", "Ali Khan DDS PC",
                 "Patel, Hetal and Bhavini"])
    assert m.best("Fare Fund Llc").found == "FareFund LLC" and m.best("Fare Fund Llc").same
    assert m.best("Hy Line Dental Associates Pc").same
    assert m.best("Rohan Chauhan").found == "Chauhan Rohanbhai" and m.best("Rohan Chauhan").same
    assert m.best("Ali Khan Dds Pc").same
    # A person is not their practice, and a couple is not a company.
    assert m.best("Ali Khan").found != "Ali Khan DDS PC"
    assert m.best("Hetalkumar Patel and Bhavini Patel").same
    assert not m.best("Kumar Patel").same


def test_confirmed_name_counts_as_that_client(conn):
    db.save_client_list(conn, "2024", ["Acme Bakery Kitchen"])
    assert reports.not_billed(conn, "2024")["rows"][0]["kind"] == "maybe"
    db.confirm_match(conn, "Acme Bakery Kitchen", "Acme Bakery, LLC")
    row = reports.not_billed(conn, "2024")["rows"][0]
    assert row["kind"] == "in_taxdome" and row["client"] == "Acme Bakery, LLC"


def test_year_written_alone_in_a_service_line():
    from feestracker.reports import tax_years_of
    assert tax_years_of("", "2026-11-12", ["FBAR - Ashish - 2025", "Forms - ty 2024"]) == {"2024", "2025"}
    assert tax_years_of("", "2026-01-26", ["Tax Return Preparation -2024"]) == {"2024"}
    assert tax_years_of("", "2024-01-15", ["Due January 16 2024"]) == {"2023"}  # a date, not a tax year
    assert tax_years_of("", "2025-05-01", ["Paid 4/15/2025"]) == {"2024"}


def test_copied_invoice_with_old_tax_year(conn):
    """Invoices copied each year keep "Tax Year 2023"; later copies count for the year before their date."""
    from feestracker import reports
    rows = [("1", "2024-04-11", "Tax Year 2023"), ("2", "2025-04-05", "Tax Year 2023"),
            ("3", "2026-04-06", "Tax Year 2023"), ("4", "2026-05-01", "Tax Year 2022")]
    for number, when, text in rows:
        conn.execute("INSERT INTO invoices (number, status, client, description, invoice_date, total_cents,"
                     " paid_cents, balance_cents) VALUES (?, 'paid', 'Copy Client', ?, ?, 100, 100, 0)",
                     (number, text, when))
    # Invoice 4 is the first to name 2022, so a late return billed in 2026 still counts for 2022.
    assert reports.client_tax_years(conn)["Copy Client"] == {"2022", "2023", "2024", "2025"}
