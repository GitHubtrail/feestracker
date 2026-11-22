"""The questions the app answers: who owes, who paid, and how much by year."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
import re
from typing import Iterable
from datetime import date

from . import db
from .clientlists import Matcher


# Age buckets for money still owed, counted from the invoice date.
AGE_BUCKETS = (
    ("0_30", "Up to 30 days", 0, 30),
    ("31_90", "31 to 90 days", 31, 90),
    ("91_365", "91 days to 1 year", 91, 365),
    ("over_1y", "Over 1 year", 366, None),
)


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def age_bucket(invoice_date: str, today: date) -> str:
    d = _day(invoice_date)
    days = (today - d).days if d else 0
    for key, _label, low, high in AGE_BUCKETS:
        if days >= low and (high is None or days <= high):
            return key
    return AGE_BUCKETS[0][0]  # future-dated invoices count as current


@dataclass
class Summary:
    invoices: int
    billed_cents: int
    collected_cents: int
    outstanding_cents: int
    open_invoices: int
    clients_owing: int
    written_off_cents: int
    written_off_invoices: int
    overpaid_cents: int
    overpaid_invoices: int
    last_import: str


def summary(conn: sqlite3.Connection) -> Summary:
    row = conn.execute(
        """SELECT COUNT(*) AS n,
                  COALESCE(SUM(total_cents), 0) AS billed,
                  COALESCE(SUM(collected_cents), 0) AS collected,
                  COALESCE(SUM(owed_cents), 0) AS outstanding,
                  COALESCE(SUM(owed_cents > 0), 0) AS open_n,
                  COALESCE(SUM(overpaid_cents), 0) AS overpaid,
                  COALESCE(SUM(overpaid_cents != 0), 0) AS overpaid_n,
                  COUNT(DISTINCT CASE WHEN owed_cents > 0 THEN client END) AS owing,
                  COALESCE(SUM(written_off_cents), 0) AS written_off,
                  COALESCE(SUM(written_off_cents > 0), 0) AS written_off_n
           FROM invoice_view"""
    ).fetchone()
    last = conn.execute("SELECT imported_at FROM imports ORDER BY id DESC LIMIT 1").fetchone()
    return Summary(
        invoices=row["n"],
        billed_cents=row["billed"],
        collected_cents=row["collected"],
        outstanding_cents=row["outstanding"],
        open_invoices=row["open_n"],
        clients_owing=row["owing"],
        written_off_cents=row["written_off"],
        written_off_invoices=row["written_off_n"],
        overpaid_cents=row["overpaid"],
        overpaid_invoices=row["overpaid_n"],
        last_import=last["imported_at"] if last else "",
    )


def by_year(conn: sqlite3.Connection) -> list[dict]:
    """Billed, collected and still owed, grouped by the year of the invoice.

    Billed = collected + still owed + bad debt in every row. Money paid above
    an invoice's amount is in "overpaid", outside that sum.
    """
    rows = conn.execute(
        """SELECT substr(invoice_date, 1, 4) AS year,
                  COUNT(*) AS invoices,
                  SUM(total_cents) AS billed,
                  SUM(collected_cents) AS collected,
                  SUM(owed_cents) AS outstanding,
                  SUM(owed_cents > 0) AS open_invoices,
                  COUNT(DISTINCT CASE WHEN owed_cents > 0 THEN client END) AS clients_owing,
                  SUM(written_off_cents) AS written_off
           FROM invoice_view
           GROUP BY year
           ORDER BY year DESC"""
    ).fetchall()
    return [dict(r) | {"year": r["year"] or "No date"} for r in rows]


def who_owes(conn: sqlite3.Connection, today: date, year: str | None = None) -> list[dict]:
    """Every client with money still owed, biggest balance first, split by age."""
    sql = "SELECT client, invoice_date, owed_cents FROM invoice_view WHERE owed_cents > 0"
    params: list = []
    if year:
        sql += " AND substr(invoice_date, 1, 4) = ?"
        params.append(year)
    clients: dict[str, dict] = {}
    for r in conn.execute(sql, params):
        c = clients.setdefault(
            r["client"],
            {"client": r["client"], "owed": 0, "invoices": 0, "oldest": r["invoice_date"]}
            | {key: 0 for key, *_ in AGE_BUCKETS},
        )
        c["owed"] += r["owed_cents"]
        c["invoices"] += 1
        c["oldest"] = min(c["oldest"], r["invoice_date"])
        c[age_bucket(r["invoice_date"], today)] += r["owed_cents"]
    return sorted(clients.values(), key=lambda c: (-c["owed"], c["client"]))


def age_totals(owing: list[dict]) -> dict[str, int]:
    return {key: sum(c[key] for c in owing) for key, *_ in AGE_BUCKETS}


# What the money paid above an invoice's amount is.
OVERPAYMENT_KINDS = (
    ("card_fee", "Card processing fees",
     "The client paid a card fee on top of the invoice (about 3%)."),
    ("shared", "Same payment shown on several invoices",
     "One payment covered several invoices, and TaxDome lists it in full on each of them. "
     "It is money received once, not extra money."),
    ("other", "Other",
     "A payment on an invoice that was already paid or had nothing to pay. Worth a look in TaxDome."),
)
CARD_FEE_RATE = 0.036  # fees seen are 3%; allow a little more, plus $1 for rounding


def overpayments(conn: sqlite3.Connection) -> dict[str, dict]:
    """For each invoice paid above its amount: what the extra is and how much.

    TaxDome lists a payment that covered several invoices in full on each of them,
    so the same amount shows up on more than one invoice of the same client.
    """
    invoices = {r["number"]: dict(r) for r in conn.execute(
        "SELECT number, client, total_cents, collected_cents, overpaid_cents FROM invoice_view")}
    payments: dict[str, list[int]] = {}
    for p in conn.execute("SELECT invoice_number, amount_cents FROM payments WHERE status = 'succeeded'"):
        if p["invoice_number"] in invoices:  # not a removed client's
            payments.setdefault(p["invoice_number"], []).append(p["amount_cents"])
    listed_on: dict[tuple, set] = {}
    for number, amounts in payments.items():
        for amount in amounts:
            listed_on.setdefault((invoices[number]["client"], amount), set()).add(number)
    out = {}
    for number, inv in invoices.items():
        extra = inv["overpaid_cents"]
        if extra <= 0:
            continue
        shared = [a for a in payments.get(number, []) if len(listed_on[(inv["client"], a)]) > 1]
        # A payment that is exactly the total of all the invoices it is listed on.
        covers_all = any(
            abs(a - sum(invoices[n]["total_cents"] for n in listed_on[(inv["client"], a)])) <= max(100, a // 100)
            for a in shared)
        if covers_all:
            kind = "shared"
        elif extra <= round(inv["collected_cents"] * CARD_FEE_RATE) + 100:
            kind = "card_fee"
        elif shared:
            kind = "shared"
        else:
            kind = "other"
        out[number] = {"kind": kind, "cents": extra}
    return out


def overpayment_totals(conn: sqlite3.Connection, found: dict | None = None) -> dict[str, dict]:
    found = overpayments(conn) if found is None else found
    totals = {k: {"cents": 0, "invoices": 0} for k, *_ in OVERPAYMENT_KINDS}
    for o in found.values():
        totals[o["kind"]]["cents"] += o["cents"]
        totals[o["kind"]]["invoices"] += 1
    return totals


def counted_twice(conn: sqlite3.Connection) -> dict[str, int]:
    """Per invoice, money shown as paid that is really a payment counted on another invoice."""
    return {n: o["cents"] for n, o in overpayments(conn).items() if o["kind"] == "shared"}


def who_paid(conn: sqlite3.Connection, year: str | None = None) -> list[dict]:
    """Clients who have paid, most collected first. Year is the year it was paid.

    A payment that TaxDome lists on several invoices is counted once.
    """
    twice = counted_twice(conn)
    sql = """SELECT client, number,
                    paid_cents AS paid,
                    paid_at
             FROM invoice_view
             WHERE paid_cents > 0"""
    params: list = []
    if year:
        # Paid invoices have a paid date; part-paid ones don't, so use the invoice date.
        sql += " AND substr(COALESCE(NULLIF(paid_at, ''), invoice_date), 1, 4) = ?"
        params.append(year)
    clients: dict[str, dict] = {}
    for r in conn.execute(sql, params):
        c = clients.setdefault(r["client"], {"client": r["client"], "paid": 0, "invoices": 0, "last_paid": ""})
        c["paid"] += r["paid"] - twice.get(r["number"], 0)
        c["invoices"] += 1
        c["last_paid"] = max(c["last_paid"], r["paid_at"] or "")
    return sorted(clients.values(), key=lambda c: (-c["paid"], c["client"]))


def years(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT substr(invoice_date, 1, 4) AS y FROM invoice_view WHERE invoice_date != ''
           UNION
           SELECT DISTINCT substr(paid_at, 1, 4) FROM invoice_view WHERE paid_at != ''
           ORDER BY 1 DESC"""
    ).fetchall()
    return [r[0] for r in rows]


def clients(conn: sqlite3.Connection, search: str = "") -> list[dict]:
    rows = conn.execute(
        """SELECT client, COUNT(*) AS invoices, SUM(total_cents) AS billed,
                  SUM(paid_cents) AS paid, SUM(owed_cents) AS owed
           FROM invoice_view
           WHERE client LIKE ? ESCAPE '\\'
           GROUP BY client ORDER BY client COLLATE NOCASE""",
        ("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
    ).fetchall()
    return [dict(r) for r in rows]


def client_invoices(conn: sqlite3.Connection, client: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM invoice_view WHERE client = ? ORDER BY invoice_date DESC, number DESC""",
        (client,),
    ).fetchall()
    out = []
    for r in rows:
        inv = dict(r)
        inv["services"] = [
            dict(li)
            for li in conn.execute(
                "SELECT * FROM line_items WHERE invoice_number = ? ORDER BY kind DESC, position",
                (r["number"],),
            )
        ]
        out.append(inv)
    return out


def bad_debts(conn: sqlite3.Connection) -> list[dict]:
    """Invoices marked as bad debt, newest invoice first.

    An invoice stays listed even if it was later paid, so a mark can be undone,
    but only the unpaid part counts as written off.
    """
    rows = conn.execute(
        """SELECT v.number, v.client, v.status, v.invoice_date, v.total_cents, v.paid_cents,
                  v.balance_cents, v.written_off_cents, b.marked_at, b.note
           FROM invoice_view v JOIN bad_debts b ON b.invoice_number = v.number
           ORDER BY v.invoice_date DESC, v.number DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def problems(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT number, client, status, invoice_date, problems FROM invoice_view
           WHERE problems != '' ORDER BY invoice_date DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def unpaid_by_years(conn: sqlite3.Connection, years: list[str]) -> list[dict]:
    """Clients still owing on invoices from the given years, with one amount per year.

    Bad debts are left out. Biggest total first.
    """
    years = sorted(set(years))
    if not years:
        return []
    marks = ",".join("?" * len(years))
    rows = conn.execute(
        f"""SELECT client, substr(invoice_date, 1, 4) AS year, SUM(owed_cents) AS owed,
                   COUNT(*) AS invoices, MIN(invoice_date) AS oldest
            FROM invoice_view
            WHERE owed_cents > 0 AND substr(invoice_date, 1, 4) IN ({marks})
            GROUP BY client, year""",
        years,
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        c = out.setdefault(r["client"], {"client": r["client"], "owed": 0, "invoices": 0,
                                         "oldest": r["oldest"], "by_year": dict.fromkeys(years, 0)})
        c["by_year"][r["year"]] = r["owed"]
        c["owed"] += r["owed"]
        c["invoices"] += r["invoices"]
        c["oldest"] = min(c["oldest"], r["oldest"])
    last_paid = _last_paid(conn)
    for c in out.values():
        c["last_paid"] = last_paid.get(c["client"], "")
    return sorted(out.values(), key=lambda c: (-c["owed"], c["client"]))


def _last_paid(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] or "" for r in conn.execute(
        "SELECT client, MAX(NULLIF(paid_at, '')) FROM invoice_view WHERE paid_cents > 0 GROUP BY client")}


# The tax year an invoice is for: a year written in its services or description
# ("Tax year 2024", "TY 2024", "2024 tax return"), otherwise the year before the
# invoice date, because a year's returns are usually billed the next year.
TAX_YEAR = re.compile(
    r"(?:tax\s*year|\bty|\bfy|year)\s*[-:]?\s*(20\d\d)\b"
    r"|(20\d\d)\s*(?:tax|return|1040|1120|1065|federal|fed\b|state)", re.I)


# In a service line a year on its own is the tax year too ("FBAR - Ashish - 2025"),
# as long as it isn't part of a date like 4/15/2025.
YEAR_IN_TITLE = re.compile(r"(?<![\d/])(?<!\d[-.])(20[1-3]\d)(?!\d)(?![/.-]\d)")


# A date written out, like "Due January 16 2024": its year is not a tax year.
DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(st|nd|rd|th)?,?\s+20\d\d",
                  re.I)


def tax_years_of(text: str, invoice_date: str, titles: Iterable[str] = ()) -> set[str]:
    named = {a or b for a, b in TAX_YEAR.findall(text)}
    for title in titles:
        title = DATE.sub(" ", title)
        named.update(a or b for a, b in TAX_YEAR.findall(title))
        named.update(YEAR_IN_TITLE.findall(title))
    if named:
        return named
    return {str(int(invoice_date[:4]) - 1)} if invoice_date[:4].isdigit() else set()


def client_tax_years(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Per TaxDome client, the tax years they have been invoiced for."""
    titles: dict[str, list[str]] = {}
    for r in conn.execute("SELECT invoice_number, title FROM line_items WHERE kind = 'service'"):
        titles.setdefault(r["invoice_number"], []).append(r["title"])
    out: dict[str, set[str]] = {}
    for r in conn.execute("SELECT number, client, invoice_date, description FROM invoices"):
        out.setdefault(r["client"], set()).update(
            tax_years_of(r["description"] or "", r["invoice_date"], titles.get(r["number"], [])))
    return out


def not_billed(conn: sqlite3.Connection, year: str) -> dict:
    """Clients on the client list for a tax year who have no invoice for that tax year.

    Clients in TaxDome come first, then names that may be in TaxDome under another
    spelling, then clients with no TaxDome invoices at all.
    """
    names = db.client_list_names(conn, year)
    years = client_tax_years(conn)
    owed = {r["client"]: r["owed"] for r in conn.execute(
        "SELECT client, SUM(owed_cents) AS owed FROM invoice_view GROUP BY client")}
    last = {r["client"]: r["last"] for r in conn.execute(
        "SELECT client, MAX(invoice_date) AS last FROM invoice_view GROUP BY client")}
    taxdome = Matcher(list(years))
    removed = set(db.removed_clients(conn))
    confirmed = db.name_matches(conn)
    skipped = set(db.not_filed(conn, year))
    skipped |= {n for n, y in db.inactive_clients(conn).items() if y <= year}
    rows = []
    for name in names:
        m = taxdome.best(name)
        client = m.found if m.same else None
        if confirmed.get(name) in years:
            client = confirmed[name]
        if name in removed or client in removed or name in skipped or client in skipped:
            continue
        billed = sorted(years.get(client, set())) if client else []
        if year in billed:
            continue
        kind = "in_taxdome" if client else ("maybe" if m.maybe else "never")
        rows.append({"name": name, "client": client, "maybe": m.found if kind == "maybe" else None,
                     "kind": kind, "tax_years": billed, "last_invoice": (last.get(client) or "")[:10],
                     "owed": owed.get(client, 0) if client else 0})
    order = {"in_taxdome": 0, "maybe": 1, "never": 2}
    rows.sort(key=lambda r: (order[r["kind"]], r["name"].lower()))
    return {"year": year, "listed": len(names), "rows": rows}
