"""SQLite storage for imported invoices.

Importing the same export twice is safe: each invoice is replaced by its
latest version, matched on the TaxDome invoice number.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .taxdome import Invoice

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    number          TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    client          TEXT NOT NULL,
    assigned_to     TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT '',
    invoice_date    TEXT NOT NULL DEFAULT '',
    paid_at         TEXT NOT NULL DEFAULT '',
    payment_methods TEXT NOT NULL DEFAULT '',
    timezone        TEXT NOT NULL DEFAULT '',
    total_cents     INTEGER NOT NULL,
    paid_cents      INTEGER NOT NULL,
    balance_cents   INTEGER NOT NULL,
    problems        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS invoices_client ON invoices(client);

CREATE TABLE IF NOT EXISTS line_items (
    invoice_number TEXT NOT NULL REFERENCES invoices(number) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('service', 'discount', 'payment')),
    position       INTEGER NOT NULL,
    title          TEXT NOT NULL,
    amount_cents   INTEGER NOT NULL,
    quantity       TEXT NOT NULL,
    taxes          TEXT NOT NULL DEFAULT '',
    total_cents    INTEGER NOT NULL,
    PRIMARY KEY (invoice_number, kind, position)
);

CREATE TABLE IF NOT EXISTS payments (
    invoice_number TEXT NOT NULL REFERENCES invoices(number) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    creator        TEXT NOT NULL DEFAULT '',
    amount_cents   INTEGER NOT NULL,
    status         TEXT NOT NULL,
    PRIMARY KEY (invoice_number, position)
);

-- Invoices you have decided will never be paid. Kept apart from the imported
-- data (and keyed only by invoice number) so a new import never loses them.
CREATE TABLE IF NOT EXISTS bad_debts (
    invoice_number TEXT PRIMARY KEY,
    marked_at      TEXT NOT NULL,
    note           TEXT NOT NULL DEFAULT ''
);

-- What the reports read: each invoice, with its unpaid balance split into
-- money still owed and money written off as bad debt.
CREATE VIEW IF NOT EXISTS invoice_view AS
SELECT i.*,
       b.invoice_number IS NOT NULL AS bad_debt,
       CASE WHEN b.invoice_number IS NULL THEN i.balance_cents ELSE 0 END AS owed_cents,
       CASE WHEN b.invoice_number IS NULL THEN 0 ELSE i.balance_cents END AS written_off_cents
FROM invoices i LEFT JOIN bad_debts b ON b.invoice_number = i.number;

-- Your own client lists, one per year, uploaded to compare years. Kept apart
-- from TaxDome data so importing invoices never touches them.
CREATE TABLE IF NOT EXISTS client_lists (
    year        TEXT PRIMARY KEY,
    source      TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_list_names (
    year     TEXT NOT NULL REFERENCES client_lists(year) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name     TEXT NOT NULL,
    PRIMARY KEY (year, position)
);

CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    invoices    INTEGER NOT NULL,
    added       INTEGER NOT NULL,
    updated     INTEGER NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def save_invoices(
    conn: sqlite3.Connection, invoices: Iterable[Invoice], source: str
) -> dict[str, int]:
    """Store invoices, replacing any with the same number. Returns counts."""
    added = updated = 0
    with conn:
        for inv in invoices:
            existed = conn.execute(
                "SELECT 1 FROM invoices WHERE number = ?", (inv.number,)
            ).fetchone()
            conn.execute("DELETE FROM invoices WHERE number = ?", (inv.number,))
            conn.execute(
                """INSERT INTO invoices (number, status, client, assigned_to, description,
                       created_at, updated_at, invoice_date, paid_at, payment_methods,
                       timezone, total_cents, paid_cents, balance_cents, problems)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    inv.number, inv.status, inv.client, inv.assigned_to, inv.description,
                    inv.created_at, inv.updated_at, inv.invoice_date, inv.paid_at,
                    inv.payment_methods, inv.timezone, inv.total_cents, inv.paid_cents,
                    inv.balance_cents, "\n".join(inv.problems()),
                ),
            )
            conn.executemany(
                """INSERT INTO line_items (invoice_number, kind, position, title,
                       amount_cents, quantity, taxes, total_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (inv.number, li.kind, li.position, li.title, li.amount_cents,
                     str(li.quantity), li.taxes, li.total_cents)
                    for li in inv.line_items
                ],
            )
            conn.executemany(
                """INSERT INTO payments (invoice_number, position, creator, amount_cents, status)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (inv.number, p.position, p.creator, p.amount_cents, p.status)
                    for p in inv.payments
                ],
            )
            if existed:
                updated += 1
            else:
                added += 1
        conn.execute(
            "INSERT INTO imports (source, imported_at, invoices, added, updated) VALUES (?, ?, ?, ?, ?)",
            (source, datetime.now().isoformat(timespec="seconds"), added + updated, added, updated),
        )
    return {"invoices": added + updated, "added": added, "updated": updated}


def mark_bad_debt(conn: sqlite3.Connection, number: str, note: str = "") -> bool:
    """Write an invoice off as bad debt. Returns False if there is no such invoice."""
    if not conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
        return False
    with conn:
        conn.execute(
            """INSERT INTO bad_debts (invoice_number, marked_at, note) VALUES (?, ?, ?)
               ON CONFLICT(invoice_number) DO UPDATE SET note = excluded.note""",
            (number, datetime.now().isoformat(timespec="seconds"), note.strip()),
        )
    return True


def unmark_bad_debt(conn: sqlite3.Connection, number: str) -> None:
    with conn:
        conn.execute("DELETE FROM bad_debts WHERE invoice_number = ?", (number,))


def save_client_list(conn: sqlite3.Connection, year: str, names: list[str], source: str = "") -> None:
    """Store the client list for a year, replacing any list already saved for it."""
    with conn:
        conn.execute("DELETE FROM client_lists WHERE year = ?", (year,))
        conn.execute(
            "INSERT INTO client_lists (year, source, uploaded_at) VALUES (?, ?, ?)",
            (year, source, datetime.now().isoformat(timespec="seconds")),
        )
        conn.executemany(
            "INSERT INTO client_list_names (year, position, name) VALUES (?, ?, ?)",
            [(year, i, n) for i, n in enumerate(names)],
        )


def client_lists(conn: sqlite3.Connection) -> list[dict]:
    """The saved lists, newest year first, with how many names each has."""
    rows = conn.execute(
        """SELECT l.year, l.source, l.uploaded_at, COUNT(n.name) AS names
           FROM client_lists l LEFT JOIN client_list_names n ON n.year = l.year
           GROUP BY l.year ORDER BY l.year DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def client_list_names(conn: sqlite3.Connection, year: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM client_list_names WHERE year = ? ORDER BY position", (year,))]


def delete_client_list(conn: sqlite3.Connection, year: str) -> None:
    with conn:
        conn.execute("DELETE FROM client_lists WHERE year = ?", (year,))
