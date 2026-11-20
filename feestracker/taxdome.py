"""Parse a TaxDome invoice export ("bills" CSV) into plain Python objects.

TaxDome writes one wide row per invoice. Each row carries up to a fixed number
of payments, service lines and discount lines in numbered columns such as
"Payment amount #1" or "Service title #12". This module turns each row into an
Invoice with lists of LineItem and Payment, and does all money maths in whole
cents so totals never drift.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator, TextIO

# Column groups as TaxDome names them. The numbers after "#" vary by export,
# so they are discovered from the header rather than hard coded.
_NUMBERED = re.compile(r"^(?P<group>.+?) #(?P<n>\d+)$")

REQUIRED_COLUMNS = ("Invoice", "Status", "Client Account Name")

SUCCEEDED = "succeeded"

# Payments taken outside TaxDome (Zelle, cash, cheque) are often recorded as a
# negative "discount" line such as "Payment Received on 5/9/2026". Those count
# as money collected, not as a discount.
OFFLINE_PAYMENT = re.compile(
    r"pa[yi]ment\s+(rec|made)|received\s+payment|amount\s+received|cash\s+received|paid\s+by",
    re.IGNORECASE,
)


class ImportError_(ValueError):
    """Raised when the file is not a TaxDome bills export we can read."""


@dataclass
class LineItem:
    kind: str  # "service", "discount", or "payment" (an offline payment line)
    position: int
    title: str
    amount_cents: int
    quantity: Decimal
    taxes: str

    @property
    def total_cents(self) -> int:
        return _round_cents(Decimal(self.amount_cents) * self.quantity)


@dataclass
class Payment:
    position: int
    creator: str
    amount_cents: int
    status: str

    @property
    def succeeded(self) -> bool:
        return self.status == SUCCEEDED


@dataclass
class Invoice:
    number: str
    status: str
    client: str
    assigned_to: str
    description: str
    created_at: str
    updated_at: str
    invoice_date: str
    paid_at: str
    payment_methods: str
    timezone: str
    line_items: list[LineItem] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)

    @property
    def services(self) -> list[LineItem]:
        return [li for li in self.line_items if li.kind == "service"]

    @property
    def discounts(self) -> list[LineItem]:
        return [li for li in self.line_items if li.kind == "discount"]

    @property
    def offline_payments(self) -> list[LineItem]:
        return [li for li in self.line_items if li.kind == "payment"]

    @property
    def total_cents(self) -> int:
        """Services plus discounts (discounts are negative in the export)."""
        return sum(li.total_cents for li in self.line_items if li.kind != "payment")

    @property
    def paid_cents(self) -> int:
        """Payments that went through, plus offline payments noted on the invoice.

        Failed or pending online payments don't count.
        """
        online = sum(p.amount_cents for p in self.payments if p.succeeded)
        offline = -sum(li.total_cents for li in self.offline_payments)
        return online + offline

    @property
    def balance_cents(self) -> int:
        """What is still owed. Paid invoices owe nothing, whatever the lines say."""
        if self.status == "paid":
            return 0
        return max(self.total_cents - self.paid_cents, 0)

    def problems(self) -> list[str]:
        """Inconsistencies in the export worth a human look. Never fatal."""
        found = []
        if self.status == "paid" and self.paid_cents != self.total_cents:
            found.append(
                f"marked paid but payments ({format_cents(self.paid_cents)}) "
                f"differ from line items ({format_cents(self.total_cents)})"
            )
        if not self.services and self.status != "paid":
            found.append("has no service lines")
        if self.status != "paid" and self.services and self.balance_cents == 0:
            found.append(f"marked {self.status} but nothing is left to pay")
        return found


def parse_money(text: str) -> int:
    """'$1,155.00' -> 115500, '-$10.00' -> -1000, '' -> 0."""
    text = (text or "").strip()
    if not text:
        return 0
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    cleaned = text.strip("()-").replace("$", "").replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"not a money amount: {text!r}") from exc
    cents = _round_cents(value * 100)
    return -cents if negative else cents


def parse_quantity(text: str) -> Decimal:
    text = (text or "").strip()
    if not text:
        return Decimal(1)
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"not a quantity: {text!r}") from exc


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rem:02d}"


def _round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _numbers(header: Iterable[str], group: str) -> list[int]:
    out = set()
    for col in header:
        m = _NUMBERED.match(col)
        if m and m.group("group") == group:
            out.add(int(m.group("n")))
    return sorted(out)


def read_invoices(source: str | Path | TextIO) -> Iterator[Invoice]:
    """Yield one Invoice per row of a TaxDome bills export."""
    if isinstance(source, (str, Path)):
        with open(source, newline="", encoding="utf-8-sig") as fh:
            yield from read_invoices(fh)
        return

    reader = csv.DictReader(source)
    header = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ImportError_(
            "This doesn't look like a TaxDome bills export. Missing columns: "
            + ", ".join(missing)
        )

    payment_ns = _numbers(header, "Payment amount")
    service_ns = _numbers(header, "Service amount")
    discount_ns = _numbers(header, "Discount amount")

    for line_no, row in enumerate(reader, start=2):
        get = lambda col: (row.get(col) or "").strip()  # noqa: E731
        try:
            inv = Invoice(
                number=get("Invoice"),
                status=get("Status").lower(),
                client=get("Client Account Name"),
                assigned_to=get("Assigned to"),
                description=row.get("Description") or "",
                created_at=get("Created"),
                updated_at=get("Updated"),
                invoice_date=get("Date"),
                paid_at=get("Paid"),
                payment_methods=get("Payment method"),
                timezone=get("Timezone"),
            )
            for kind, label, ns in (
                ("service", "Service", service_ns),
                ("discount", "Discount", discount_ns),
            ):
                for n in ns:
                    title = get(f"{label} title #{n}")
                    amount = get(f"{label} amount #{n}")
                    if not title and not amount:
                        continue
                    if kind == "discount" and OFFLINE_PAYMENT.search(title):
                        kind_here = "payment"
                    else:
                        kind_here = kind
                    inv.line_items.append(
                        LineItem(
                            kind=kind_here,
                            position=n,
                            title=title,
                            amount_cents=parse_money(amount),
                            quantity=parse_quantity(get(f"{label} quantity #{n}")),
                            taxes=get(f"{label} taxes #{n}"),
                        )
                    )
            for n in payment_ns:
                amount = get(f"Payment amount #{n}")
                status = get(f"Payment status #{n}")
                if not amount and not status:
                    continue
                inv.payments.append(
                    Payment(
                        position=n,
                        creator=get(f"Payment creator #{n}"),
                        amount_cents=parse_money(amount),
                        status=status.lower(),
                    )
                )
        except ValueError as exc:
            raise ImportError_(f"Row {line_no} (invoice {get('Invoice') or '?'}): {exc}") from exc
        if not inv.number:
            raise ImportError_(f"Row {line_no}: invoice number is empty")
        yield inv
