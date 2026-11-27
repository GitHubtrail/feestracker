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

-- Names on your client lists that you confirmed are a TaxDome client spelled
-- another way, so the Not billed page treats them as that client.
CREATE TABLE IF NOT EXISTS name_matches (
    list_name    TEXT PRIMARY KEY,
    client       TEXT NOT NULL,
    confirmed_at TEXT NOT NULL
);

-- Guesses you said are wrong: this name on a client list is NOT this TaxDome client.
CREATE TABLE IF NOT EXISTS not_matches (
    list_name  TEXT NOT NULL,
    client     TEXT NOT NULL,
    marked_at  TEXT NOT NULL,
    PRIMARY KEY (list_name, client)
);

-- Names on a year's list that have an accepted federal return that year, from the
-- e-file list, so "Not my client" answers aren't carried to a year they filed.
CREATE TABLE IF NOT EXISTS efiled_names (
    year TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (year, name)
);

-- Clients billed for a tax year on an invoice the app counts for another year
-- (one invoice covering 2023 and 2024), so they count as billed for that year.
CREATE TABLE IF NOT EXISTS covered_years (
    year      TEXT NOT NULL,
    name      TEXT NOT NULL,
    marked_at TEXT NOT NULL,
    PRIMARY KEY (year, name)
);

-- Clients on a year's client list that you didn't file for that year, so the
-- Not billed page leaves them out for that year only.
CREATE TABLE IF NOT EXISTS not_filed (
    year      TEXT NOT NULL,
    name      TEXT NOT NULL,
    marked_at TEXT NOT NULL,
    PRIMARY KEY (year, name)
);

-- Clients who are no longer yours from a tax year on, so you know not to bill
-- them. Their invoices, payments and bad debts stay as they are.
CREATE TABLE IF NOT EXISTS inactive_clients (
    name      TEXT PRIMARY KEY,
    from_year TEXT NOT NULL,
    marked_at TEXT NOT NULL
);

-- People billed on another client's invoice, usually their S Corp's: they count
-- as billed for a tax year whenever that company has an invoice for it.
CREATE TABLE IF NOT EXISTS billed_with (
    person    TEXT PRIMARY KEY,
    company   TEXT NOT NULL,
    linked_at TEXT NOT NULL
);

-- Clients whose returns you do for free and never bill, so they never show as
-- not billed. Their invoices, if any, stay as they are.
CREATE TABLE IF NOT EXISTS free_clients (
    name      TEXT PRIMARY KEY,
    marked_at TEXT NOT NULL
);

-- The return name your tax software shows for a client on a client list, when it
-- differs from the name kept: TaxDome is often spelled like it.
CREATE TABLE IF NOT EXISTS list_aliases (
    name  TEXT PRIMARY KEY,
    alias TEXT NOT NULL
);

-- Single invoices you removed, usually ones deleted in TaxDome (an import only adds
-- and updates, so it can't tell). Left out of every page and total until put back.
CREATE TABLE IF NOT EXISTS removed_invoices (
    number     TEXT PRIMARY KEY,
    removed_at TEXT NOT NULL
);

-- Clients you removed. Their invoices are left out of every page and total, and
-- stay left out after a new TaxDome import, until you put them back.
CREATE TABLE IF NOT EXISTS removed_clients (
    name       TEXT PRIMARY KEY,
    removed_at TEXT NOT NULL
);

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
       LEFT JOIN bad_debts b ON b.invoice_number = i.number
       WHERE i.client NOT IN (SELECT name FROM removed_clients)
         AND i.number NOT IN (SELECT number FROM removed_invoices))
SELECT v.*,
       -- What was paid toward the invoice itself, so billed always equals
       -- collected + owed + written off. Anything paid above the invoice amount
       -- (one payment covering several invoices, a fee) is kept apart.
       total_cents - balance_cents AS collected_cents,
       paid_cents - (total_cents - balance_cents) AS overpaid_cents,
       CASE WHEN bad_debt THEN 0 ELSE balance_cents END AS owed_cents,
       CASE WHEN bad_debt THEN balance_cents ELSE 0 END AS written_off_cents
FROM v;

-- Your own client lists, one per tax year, to see who wasn't billed. Kept apart
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


def client_list_source(conn: sqlite3.Connection, year: str) -> str:
    row = conn.execute("SELECT source FROM client_lists WHERE year = ?", (year,)).fetchone()
    return row[0] if row else ""


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


def save_aliases(conn: sqlite3.Connection, aliases: dict[str, str]) -> None:
    with conn:
        conn.executemany("INSERT OR REPLACE INTO list_aliases (name, alias) VALUES (?, ?)", aliases.items())


def list_aliases(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT name, alias FROM list_aliases")}


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


def remove_clients(conn: sqlite3.Connection, names: Iterable[str]) -> int:
    """Hide clients from every page. Nothing is deleted, so they can be put back."""
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        return conn.executemany(
            "INSERT OR IGNORE INTO removed_clients (name, removed_at) VALUES (?, ?)",
            [(n, now) for n in {n.strip() for n in names} if n]).rowcount


def restore_clients(conn: sqlite3.Connection, names: Iterable[str]) -> int:
    with conn:
        return conn.executemany("DELETE FROM removed_clients WHERE name = ?",
                                [(n,) for n in set(names)]).rowcount


def remove_invoices(conn: sqlite3.Connection, numbers: Iterable[str]) -> int:
    """Take invoices out of every page and total. Bills outside TaxDome are deleted instead."""
    now = datetime.now().isoformat(timespec="seconds")
    numbers = set(numbers)
    for n in [n for n in numbers if is_outside(n)]:
        delete_outside_bill(conn, n)
    with conn:
        return conn.executemany("INSERT OR IGNORE INTO removed_invoices (number, removed_at) VALUES (?, ?)",
                                [(n, now) for n in numbers if n and not is_outside(n)]).rowcount


def restore_invoices(conn: sqlite3.Connection, numbers: Iterable[str]) -> None:
    with conn:
        conn.executemany("DELETE FROM removed_invoices WHERE number = ?", [(n,) for n in set(numbers)])


def removed_invoices(conn: sqlite3.Connection, client: str | None = None) -> list[dict]:
    sql = """SELECT i.number, i.client, i.invoice_date, i.total_cents, r.removed_at
             FROM removed_invoices r JOIN invoices i ON i.number = r.number"""
    params: tuple = ()
    if client is not None:
        sql += " WHERE i.client = ?"
        params = (client,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY i.invoice_date DESC", params)]


def missing_from_import(conn: sqlite3.Connection, numbers: set[str]) -> list[dict]:
    """TaxDome invoices here that a full export didn't have: most likely deleted in TaxDome."""
    return [dict(r) for r in conn.execute(
        """SELECT number, client, invoice_date, total_cents FROM invoice_view
           WHERE number NOT LIKE 'OUT-%' ORDER BY invoice_date DESC""") if r["number"] not in numbers]


def removed_clients(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM removed_clients ORDER BY name COLLATE NOCASE")]


def confirm_match(conn: sqlite3.Connection, list_name: str, client: str) -> None:
    """Remember that a name on a client list is this TaxDome client."""
    with conn:
        conn.execute("INSERT OR REPLACE INTO name_matches (list_name, client, confirmed_at) VALUES (?, ?, ?)",
                     (list_name, client, datetime.now().isoformat(timespec="seconds")))


def name_matches(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT list_name, client FROM name_matches")}


def forget_matches(conn: sqlite3.Connection, list_names: Iterable[str]) -> None:
    """Undo "same client" answers, so these list names are matched (or asked about) again."""
    with conn:
        conn.executemany("DELETE FROM name_matches WHERE list_name = ?", [(n,) for n in set(list_names)])


def reject_match(conn: sqlite3.Connection, list_name: str, client: str) -> None:
    """Remember that a name on a client list is not this TaxDome client, so it isn't guessed again."""
    with conn:
        conn.execute("INSERT OR IGNORE INTO not_matches (list_name, client, marked_at) VALUES (?, ?, ?)",
                     (list_name, client, datetime.now().isoformat(timespec="seconds")))
        conn.execute("DELETE FROM name_matches WHERE list_name = ? AND client = ?", (list_name, client))


def not_matches(conn: sqlite3.Connection) -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for name, client in conn.execute("SELECT list_name, client FROM not_matches"):
        out.setdefault(name, set()).add(client)
    return {k: frozenset(v) for k, v in out.items()}


def link_billed_with(conn: sqlite3.Connection, people: Iterable[str], company: str) -> int:
    """Note that these people are billed on the company's invoice."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = [(p, company, now) for p in set(people) if p.strip() and p != company]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO billed_with (person, company, linked_at) VALUES (?, ?, ?)", rows)
    return len(rows)


def unlink_billed_with(conn: sqlite3.Connection, people: Iterable[str]) -> None:
    with conn:
        conn.executemany("DELETE FROM billed_with WHERE person = ?", [(p,) for p in set(people)])


def billed_with(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT person, company FROM billed_with ORDER BY person")}


def set_free(conn: sqlite3.Connection, names: Iterable[str], free: bool = True) -> None:
    """Mark clients whose returns you do for free (or, with free=False, undo it)."""
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        if free:
            conn.executemany("INSERT OR IGNORE INTO free_clients (name, marked_at) VALUES (?, ?)",
                             [(n, now) for n in set(names) if n.strip()])
        else:
            conn.executemany("DELETE FROM free_clients WHERE name = ?", [(n,) for n in set(names)])


def free_clients(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM free_clients ORDER BY name")]


# ---------------------------------------------------------------- bills outside TaxDome

OUTSIDE = "outside TaxDome"   # the status these bills carry
OUTSIDE_PREFIX = "OUT-"       # their numbers; TaxDome's are digits only, so an import never touches them


def is_outside(number: str) -> bool:
    return number.startswith(OUTSIDE_PREFIX)


def _outside_rows(client: str, tax_year: str, amount_cents: int, note: str) -> tuple[str, str]:
    title = f"Tax year {tax_year}" + (f" - {note.strip()}" if note.strip() else "")
    return title, f"Billed outside TaxDome. {title}"


def add_outside_bill(conn: sqlite3.Connection, client: str, tax_year: str, amount_cents: int, billed_on: str,
                     note: str = "", received_cents: int = 0, received_on: str = "") -> str:
    """Add a bill you sent outside TaxDome, and what you already received for it.
    It then counts everywhere like a TaxDome invoice. Returns its number."""
    client = " ".join(client.split())
    if not client:
        raise ValueError("client name is missing")
    if amount_cents < 0 or received_cents < 0:
        raise ValueError("amounts can't be negative")
    title, description = _outside_rows(client, tax_year, amount_cents, note)
    with conn:
        last = conn.execute(
            "SELECT MAX(CAST(substr(number, ?) AS INTEGER)) FROM invoices WHERE number LIKE ?",
            (len(OUTSIDE_PREFIX) + 1, OUTSIDE_PREFIX + "%")).fetchone()[0] or 0
        number = f"{OUTSIDE_PREFIX}{last + 1}"
        conn.execute(
            """INSERT INTO invoices (number, status, client, description, created_at, invoice_date,
                   payment_methods, total_cents, paid_cents, balance_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (number, OUTSIDE, client, description, datetime.now().isoformat(timespec="seconds"),
             billed_on, OUTSIDE, amount_cents, amount_cents))
        conn.execute(
            """INSERT INTO line_items (invoice_number, kind, position, title, amount_cents, quantity, total_cents)
               VALUES (?, 'service', 1, ?, ?, '1', ?)""", (number, title, amount_cents, amount_cents))
        if received_cents:
            conn.execute(
                """INSERT INTO manual_payments (invoice_number, paid_on, amount_cents, note, entered_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (number, received_on or billed_on, received_cents, "Outside TaxDome",
                 datetime.now().isoformat(timespec="seconds")))
    return number


def update_outside_bill(conn: sqlite3.Connection, number: str, client: str, tax_year: str, amount_cents: int,
                        billed_on: str, note: str = "") -> bool:
    if not is_outside(number) or amount_cents < 0:
        return False
    client = " ".join(client.split())
    title, description = _outside_rows(client, tax_year, amount_cents, note)
    with conn:
        n = conn.execute(
            """UPDATE invoices SET client = ?, description = ?, invoice_date = ?, total_cents = ?,
                   balance_cents = ? WHERE number = ?""",
            (client, description, billed_on, amount_cents, amount_cents, number)).rowcount
        conn.execute("UPDATE line_items SET title = ?, amount_cents = ?, total_cents = ? WHERE invoice_number = ?",
                     (title, amount_cents, amount_cents, number))
    return bool(n)


def delete_outside_bill(conn: sqlite3.Connection, number: str) -> None:
    if not is_outside(number):
        return
    with conn:
        conn.execute("DELETE FROM manual_payments WHERE invoice_number = ?", (number,))
        conn.execute("DELETE FROM bad_debts WHERE invoice_number = ?", (number,))
        conn.execute("DELETE FROM invoices WHERE number = ?", (number,))


def outside_bills(conn: sqlite3.Connection) -> list[dict]:
    """Bills outside TaxDome, newest first, with the tax year and note split back out."""
    out = []
    for r in conn.execute(
            """SELECT v.*, l.title FROM invoice_view v JOIN line_items l ON l.invoice_number = v.number
               WHERE v.number LIKE ? ORDER BY v.invoice_date DESC, CAST(substr(v.number, ?) AS INTEGER) DESC""",
            (OUTSIDE_PREFIX + "%", len(OUTSIDE_PREFIX) + 1)):
        d = dict(r)
        head, _, note = d["title"].partition(" - ")
        d["tax_year"], d["note"] = head.removeprefix("Tax year "), note
        out.append(d)
    return out


def mark_not_filed(conn: sqlite3.Connection, year: str, names: Iterable[str], filed: bool = False) -> None:
    """Note that you didn't file these clients for a tax year (or, with filed=True, undo it)."""
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        if filed:
            conn.executemany("DELETE FROM not_filed WHERE year = ? AND name = ?", [(year, n) for n in set(names)])
        else:
            conn.executemany("INSERT OR IGNORE INTO not_filed (year, name, marked_at) VALUES (?, ?, ?)",
                             [(year, n, now) for n in set(names) if n.strip()])


def save_efiled(conn: sqlite3.Connection, year: str, names: Iterable[str]) -> None:
    with conn:
        conn.execute("DELETE FROM efiled_names WHERE year = ?", (year,))
        conn.executemany("INSERT OR IGNORE INTO efiled_names (year, name) VALUES (?, ?)",
                         [(year, n) for n in set(names)])


def efiled(conn: sqlite3.Connection, year: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM efiled_names WHERE year = ?", (year,))}


def mark_covered(conn: sqlite3.Connection, year: str, names: Iterable[str], undo: bool = False) -> None:
    """Note that these clients were billed for a tax year on another invoice (or undo it)."""
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        if undo:
            conn.executemany("DELETE FROM covered_years WHERE year = ? AND name = ?", [(year, n) for n in set(names)])
        else:
            conn.executemany("INSERT OR IGNORE INTO covered_years (year, name, marked_at) VALUES (?, ?, ?)",
                             [(year, n, now) for n in set(names) if n.strip()])


def covered(conn: sqlite3.Connection, year: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM covered_years WHERE year = ? ORDER BY name COLLATE NOCASE", (year,))]


def not_filed(conn: sqlite3.Connection, year: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM not_filed WHERE year = ? ORDER BY name COLLATE NOCASE", (year,))]


def set_inactive(conn: sqlite3.Connection, names: Iterable[str], from_year: str | None) -> None:
    """Mark clients inactive from a tax year on, or active again with from_year=None."""
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        if from_year is None:
            conn.executemany("DELETE FROM inactive_clients WHERE name = ?", [(n,) for n in set(names)])
        else:
            conn.executemany(
                "INSERT OR REPLACE INTO inactive_clients (name, from_year, marked_at) VALUES (?, ?, ?)",
                [(n, from_year, now) for n in set(names) if n.strip()])


def inactive_clients(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT name, from_year FROM inactive_clients")}
