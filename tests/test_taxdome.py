import io
from decimal import Decimal

import pytest

from feestracker.taxdome import ImportError_, format_cents, parse_money, read_invoices


@pytest.mark.parametrize(
    "text, cents",
    [("$1,155.00", 115500), ("-$10.00", -1000), ("", 0), ("$0.05", 5), ("($2.50)", -250)],
)
def test_parse_money(text, cents):
    assert parse_money(text) == cents


def test_parse_money_rejects_junk():
    with pytest.raises(ValueError):
        parse_money("ten dollars")


def test_format_cents():
    assert format_cents(123456789) == "$1,234,567.89"
    assert format_cents(-1000) == "-$10.00"
    assert format_cents(0) == "$0.00"


def test_reads_every_row(invoices):
    assert sorted(invoices) == ["1001", "1002", "1003", "1004", "1005", "1006"]


def test_paid_invoice_with_discount(invoices):
    inv = invoices["1001"]
    assert inv.client == "Acme Bakery, LLC"
    assert inv.description == "Tax Year 2023\n[Includes state return]"
    assert [li.title for li in inv.services] == [
        "Form 1120S - S Corp Tax Returns",
        "Credit card convenience fee",
    ]
    assert [li.total_cents for li in inv.discounts] == [-1000]
    assert inv.total_cents == 115500
    assert inv.paid_cents == 115500
    assert inv.balance_cents == 0
    assert inv.problems() == []


def test_quantity_and_failed_payment(invoices):
    inv = invoices["1002"]
    assert inv.services[0].quantity == Decimal("12.0")
    assert inv.total_cents == 90600
    assert [p.status for p in inv.payments] == ["succeeded", "failed"]
    assert inv.paid_cents == 10000  # the failed $50 doesn't count
    assert inv.balance_cents == 80600


def test_offline_payment_line_counts_as_paid(invoices):
    inv = invoices["1003"]
    assert inv.discounts == []
    assert [li.title for li in inv.offline_payments] == ["Payment Received on 6/1/2022"]
    assert inv.total_cents == 90000
    assert inv.paid_cents == 30000
    assert inv.balance_cents == 60000


def test_flags_inconsistent_rows(invoices):
    assert invoices["1005"].balance_cents == 0
    assert invoices["1005"].problems() == ["marked overdue but nothing is left to pay"]
    assert invoices["1006"].problems() == [
        "marked paid but payments ($500.00) differ from line items ($400.00)"
    ]


def test_paid_invoice_never_owes(invoices):
    assert invoices["1006"].balance_cents == 0


def test_rejects_other_csv():
    with pytest.raises(ImportError_, match="TaxDome"):
        list(read_invoices(io.StringIO("Name,Amount\nBob,$1\n")))


def test_bad_money_names_the_row():
    text = "Invoice,Status,Client Account Name,Service title #1,Service amount #1\n7,paid,X,Fee,lots\n"
    with pytest.raises(ImportError_, match="Row 2 \\(invoice 7\\)"):
        list(read_invoices(io.StringIO(text)))


def test_offline_payment_wording_and_typos():
    from feestracker.taxdome import OFFLINE_PAYMENT
    for title in ["Payment Received on 5/9/2026", "pay received on 1/2/2024", "Payment rcd 6/3/2024 (Zelle)",
                  "Received on 3/28/2024", "Paid earlier 50%", "Fees recd 50%", "Initial consulting fee received",
                  "Panyment made on 11/19/2026", "Paymnt received"]:
        assert OFFLINE_PAYMENT.search(title), title
    for title in ["Discount", "Payable later by 12/6/2023", "Due later 50%", "Write off", "Moved to new invoice",
                  "Credit from 2021 invoice", "PTAC Fees Discount", "Additional state payroll PA TY 2021"]:
        assert not OFFLINE_PAYMENT.search(title), title
