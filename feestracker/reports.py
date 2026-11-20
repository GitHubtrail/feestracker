"""The questions the app answers: who owes, who paid, and how much by year."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

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
    last_import: str


def summary(conn: sqlite3.Connection) -> Summary:
    row = conn.execute(
        """SELECT COUNT(*) AS n,
                  COALESCE(SUM(total_cents), 0) AS billed,
                  COALESCE(SUM(paid_cents), 0) AS collected,
                  COALESCE(SUM(balance_cents), 0) AS outstanding,
                  COALESCE(SUM(balance_cents > 0), 0) AS open_n,
                  COUNT(DISTINCT CASE WHEN balance_cents > 0 THEN client END) AS owing
           FROM invoices"""
    ).fetchone()
    last = conn.execute("SELECT imported_at FROM imports ORDER BY id DESC LIMIT 1").fetchone()
    return Summary(
        invoices=row["n"],
        billed_cents=row["billed"],
        collected_cents=row["collected"],
        outstanding_cents=row["outstanding"],
        open_invoices=row["open_n"],
        clients_owing=row["owing"],
        last_import=last["imported_at"] if last else "",
    )


def by_year(conn: sqlite3.Connection) -> list[dict]:
    """Billed, collected and still owed, grouped by the year of the invoice."""
    rows = conn.execute(
        """SELECT substr(invoice_date, 1, 4) AS year,
                  COUNT(*) AS invoices,
                  SUM(total_cents) AS billed,
                  SUM(paid_cents) AS collected,
                  SUM(balance_cents) AS outstanding,
                  SUM(balance_cents > 0) AS open_invoices,
                  COUNT(DISTINCT CASE WHEN balance_cents > 0 THEN client END) AS clients_owing
           FROM invoices
           GROUP BY year
           ORDER BY year DESC"""
    ).fetchall()
    return [dict(r) | {"year": r["year"] or "No date"} for r in rows]


def who_owes(conn: sqlite3.Connection, today: date, year: str | None = None) -> list[dict]:
    """Every client with money still owed, biggest balance first, split by age."""
    sql = "SELECT client, invoice_date, balance_cents FROM invoices WHERE balance_cents > 0"
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
        c["owed"] += r["balance_cents"]
        c["invoices"] += 1
        c["oldest"] = min(c["oldest"], r["invoice_date"])
        c[age_bucket(r["invoice_date"], today)] += r["balance_cents"]
    return sorted(clients.values(), key=lambda c: (-c["owed"], c["client"]))


def age_totals(owing: list[dict]) -> dict[str, int]:
    return {key: sum(c[key] for c in owing) for key, *_ in AGE_BUCKETS}


def who_paid(conn: sqlite3.Connection, year: str | None = None) -> list[dict]:
    """Clients who have paid, most collected first. Year is the year it was paid."""
    sql = """SELECT client,
                    SUM(paid_cents) AS paid,
                    COUNT(*) AS invoices,
                    MAX(paid_at) AS last_paid
             FROM invoices
             WHERE paid_cents > 0"""
    params: list = []
    if year:
        # Paid invoices have a paid date; part-paid ones don't, so use the invoice date.
        sql += " AND substr(COALESCE(NULLIF(paid_at, ''), invoice_date), 1, 4) = ?"
        params.append(year)
    sql += " GROUP BY client ORDER BY paid DESC, client"
    return [dict(r) for r in conn.execute(sql, params)]


def years(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT substr(invoice_date, 1, 4) AS y FROM invoices WHERE invoice_date != ''
           UNION
           SELECT DISTINCT substr(paid_at, 1, 4) FROM invoices WHERE paid_at != ''
           ORDER BY 1 DESC"""
    ).fetchall()
    return [r[0] for r in rows]


def clients(conn: sqlite3.Connection, search: str = "") -> list[dict]:
    rows = conn.execute(
        """SELECT client, COUNT(*) AS invoices, SUM(total_cents) AS billed,
                  SUM(paid_cents) AS paid, SUM(balance_cents) AS owed
           FROM invoices
           WHERE client LIKE ? ESCAPE '\\'
           GROUP BY client ORDER BY client COLLATE NOCASE""",
        ("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
    ).fetchall()
    return [dict(r) for r in rows]


def client_invoices(conn: sqlite3.Connection, client: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM invoices WHERE client = ? ORDER BY invoice_date DESC, number DESC""",
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


def problems(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT number, client, status, invoice_date, problems FROM invoices
           WHERE problems != '' ORDER BY invoice_date DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
