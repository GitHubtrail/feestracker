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

-- Payments you entered in Fees Tracker yourself, for example a client who paid
-- part of an invoice by Zelle or cheque that isn't in TaxDome yet. Keyed by
-- invoice number so a new import never loses them.
CREATE TABLE IF NOT EXISTS manual_payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    paid_on        TEXT NOT NULL,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    note           TEXT NOT NULL DEFAULT '',
    entered_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS manual_payments_invoice ON manual_payments(invoice_number);

-- What the reports read: each invoice with the payments you entered added to
-- what TaxDome says was paid, and its unpaid balance split into money still
-- owed and money written off as bad debt. Recreated on every start so older
-- data folders pick up changes.
DROP VIEW IF EXISTS invoice_view;
CREATE VIEW invoice_view AS
WITH m AS (SELECT invoice_number, SUM(amount_cents) AS cents, MAX(paid_on) AS last_on
           FROM manual_payments GROUP BY invoice_number),
     v AS (
       SELECT i.number, i.status, i.client, i.assigned_to, i.description, i.created_at,
              i.updated_at, i.invoice_date, i.payment_methods, i.timezone, i.total_cents,
              i.problems,
              i.paid_cents AS taxdome_paid_cents,
              COALESCE(m.cents, 0) AS manual_paid_cents,
              i.paid_cents + COALESCE(m.cents, 0) AS paid_cents,
              MAX(i.balance_cents - COALESCE(m.cents, 0), 0) AS balance_cents,
              CASE WHEN i.paid_at = '' AND m.cents IS NOT NULL
                        AND i.balance_cents - m.cents <= 0 THEN m.last_on
                   ELSE i.paid_at END AS paid_at,
              b.invoice_number IS NOT NULL AS bad_debt
       FROM invoices i
       LEFT JOIN m ON m.invoice_number = i.number
       LEFT JOIN bad_debts b ON b.invoice_number = i.number)
SELECT v.*,
       CASE WHEN bad_debt THEN 0 ELSE balance_cents END AS owed_cents,
       CASE WHEN bad_debt THEN balance_cents ELSE 0 END AS written_off_cents
FROM v;

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


def add_payment(conn: sqlite3.Connection, number: str, amount_cents: int, paid_on: str,
                note: str = "") -> bool:
    """Record a payment you received for an invoice. Returns False if there is no such invoice."""
    if amount_cents <= 0:
        raise ValueError("amount must be more than zero")
    if not conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
        return False
    with conn:
        conn.execute(
            """INSERT INTO manual_payments (invoice_number, paid_on, amount_cents, note, entered_at)
               VALUES (?, ?, ?, ?, ?)""",
            (number, paid_on, amount_cents, note.strip(), datetime.now().isoformat(timespec="seconds")),
        )
    return True


def delete_payment(conn: sqlite3.Connection, payment_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM manual_payments WHERE id = ?", (payment_id,))


def manual_payments(conn: sqlite3.Connection, number: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM manual_payments WHERE invoice_number = ? ORDER BY paid_on, id", (number,))]
