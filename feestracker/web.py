"""A small web app on your own computer. Nothing leaves the machine.

Uses only the Python standard library, so there is nothing to install.
"""

from __future__ import annotations

import csv
import email.parser
import email.policy
import io
import re
import sqlite3
from datetime import date
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import db, reports, storage
from .taxdome import ImportError_, format_cents, read_invoices

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

STYLE = """
:root { --bg:#f7f7f5; --card:#fff; --ink:#1d1d1b; --muted:#6b6b66; --line:#e3e2dd;
        --accent:#1f5fbf; --owe:#b3261e; --ok:#1e7a3c; --warn:#8a5a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161615; --card:#201f1d; --ink:#ecebe6; --muted:#a3a29c; --line:#34332f;
          --accent:#7fb0ff; --owe:#ff8a80; --ok:#7bd88f; --warn:#f0c060; } }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink);
       font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif }
header { background:var(--card); border-bottom:1px solid var(--line); padding:12px 16px }
header nav { max-width:1100px; margin:0 auto; display:flex; flex-wrap:wrap; gap:4px 18px; align-items:center }
header b { margin-right:12px; font-size:18px }
a { color:var(--accent); text-decoration:none } a:hover { text-decoration:underline }
nav a.on { font-weight:600; text-decoration:underline }
main { max-width:1100px; margin:0 auto; padding:20px 16px 60px }
h1 { font-size:24px; margin:4px 0 16px } h2 { font-size:19px; margin:28px 0 10px }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px }
.card .label { color:var(--muted); font-size:14px } .card .big { font-size:26px; font-weight:650 }
.owe { color:var(--owe) } .ok { color:var(--ok) } .muted { color:var(--muted) } .warn { color:var(--warn) }
.wrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px }
table { border-collapse:collapse; width:100% }
th, td { padding:8px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top }
th { font-size:13px; color:var(--muted); font-weight:600; white-space:nowrap }
td.n, th.n { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums }
tr:last-child td { border-bottom:0 } tfoot td { font-weight:650 }
form.inline { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:0 0 14px }
select, input[type=search], input[type=file], button, textarea { font:inherit }
button, .btn { background:var(--accent); color:#fff; border:0; border-radius:8px; padding:8px 14px; cursor:pointer }
.pill { display:inline-block; padding:1px 8px; border-radius:99px; font-size:13px; border:1px solid var(--line) }
textarea { width:100%; padding:10px; border-radius:8px; border:1px solid var(--line);
           background:var(--bg); color:var(--ink) }
.note { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--accent);
        border-radius:8px; padding:10px 14px; margin:0 0 16px }
.small { font-size:14px }
form.act { margin:6px 0 0 }
form.act button { background:transparent; color:var(--accent); border:1px solid var(--line);
                  padding:2px 10px; font-size:13px }
.pill.bad { color:var(--muted); background:var(--bg) }
fieldset { border:1px solid var(--line); border-radius:10px; background:var(--card); padding:12px 16px; margin:0 0 16px }
fieldset label { margin-right:14px; white-space:nowrap }
details.act summary { cursor:pointer; color:var(--accent); margin-top:6px }
details.act input { font:inherit; padding:4px 6px; border:1px solid var(--line); border-radius:6px;
                    background:var(--bg); color:var(--ink) }
.stack > * { margin:0 0 10px }
"""

# The years Sarita asked to follow up on first.
DEFAULT_UNPAID_YEARS = ("2023", "2024")

NAV = [
    ("/", "Summary"),
    ("/owes", "Who owes"),
    ("/unpaid", "Unpaid by year"),
    ("/paid", "Who paid"),
    ("/clients", "All clients"),
    ("/import", "Import"),
    ("/bad-debts", "Bad debts"),
    ("/checks", "Data checks"),
    ("/data", "Your data & updates"),
]


def count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def money(cents: int | None) -> str:
    return format_cents(cents or 0)


def page(title: str, body: str, active: str = "") -> bytes:
    links = "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>'
        for href, label in NAV
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} · Fees Tracker</title><style>{STYLE}</style></head>"
        f"<body><header><nav><b>Fees Tracker</b>{links}</nav></header>"
        f"<main>{body}</main></body></html>"
    ).encode("utf-8")


def client_link(name: str) -> str:
    return f'<a href="/client?name={quote(name)}">{escape(name)}</a>'


def year_picker(path: str, years: list[str], chosen: str, label: str) -> str:
    opts = '<option value="">All years</option>' + "".join(
        f'<option{" selected" if y == chosen else ""}>{escape(y)}</option>' for y in years
    )
    return (
        f'<form class=inline method=get action="{path}"><label>{escape(label)} '
        f'<select name=year onchange="this.form.submit()">{opts}</select></label>'
        "<noscript><button>Show</button></noscript></form>"
    )


def empty_state() -> str:
    return (
        '<div class=note>No invoices yet. Go to <a href="/import">Import</a> and choose the '
        "bills export you downloaded from TaxDome. If you used Fees Tracker before, look on "
        '<a href="/data">Your data &amp; updates</a> first: your earlier data may be there.</div>'
    )


# ---------------------------------------------------------------- pages


def home(conn: sqlite3.Connection, today: date) -> bytes:
    s = reports.summary(conn)
    if not s.invoices:
        return page("Summary", "<h1>Summary</h1>" + empty_state(), "/")
    owing = reports.who_owes(conn, today)
    ages = reports.age_totals(owing)
    overpaid = ""
    if s.overpaid_cents:
        overpaid = (
            f"<p class='small muted'>Not included above: {money(s.overpaid_cents)} paid above the invoice "
            f"amount on {count(s.overpaid_invoices, 'invoice')}, for example one payment that covered "
            "several invoices. See <a href='/checks'>Data checks</a>.</p>"
        )
    cards = f"""
    <div class=cards>
      <div class=card><div class=label>Billed</div>
        <div class=big>{money(s.billed_cents)}</div>
        <div class="small muted">{s.invoices} invoices</div></div>
      <div class=card><div class=label>Collected</div>
        <div class="big ok">{money(s.collected_cents)}</div></div>
      <div class=card><div class=label>Owed to you now</div>
        <div class="big owe">{money(s.outstanding_cents)}</div>
        <div class="small muted">{s.open_invoices} invoices · {s.clients_owing} clients</div></div>
      <div class=card><div class=label>Written off as bad debt</div>
        <div class="big muted">{money(s.written_off_cents)}</div>
        <div class="small muted"><a href="/bad-debts">{count(s.written_off_invoices, 'invoice')}</a></div></div>
    </div>
    <p class="small muted">Billed = collected + owed + bad debt.</p>
    {overpaid}"""
    age_rows = "".join(
        f"<tr><td>{label}</td><td class=n>{money(ages[key])}</td></tr>"
        for key, label, *_ in reports.AGE_BUCKETS
    )
    year_rows = "".join(
        f"""<tr><td><b>{escape(y['year'])}</b></td><td class=n>{y['invoices']}</td>
            <td class=n>{money(y['billed'])}</td><td class="n ok">{money(y['collected'])}</td>
            <td class="n owe">{money(y['outstanding'])}</td>
            <td class="n muted">{money(y['written_off'])}</td>
            <td class="n muted">{money(y['overpaid']) if y['overpaid'] else '–'}</td>
            <td class=n><a href="/owes?year={quote(y['year'])}">{y['clients_owing']} clients</a></td></tr>"""
        for y in reports.by_year(conn)
    )
    top = "".join(
        f"<tr><td>{client_link(c['client'])}</td><td class='n owe'>{money(c['owed'])}</td>"
        f"<td class=n>{c['invoices']}</td><td class=muted>{escape(c['oldest'][:10])}</td></tr>"
        for c in owing[:10]
    )
    body = f"""<h1>Summary</h1>
    <p class="muted small">Last import: {escape(s.last_import.replace('T', ' ')) or 'never'}</p>
    {cards}
    <h2>Year by year</h2>
    <div class=wrap><table><thead><tr><th>Invoice year</th><th class=n>Invoices</th>
      <th class=n>Billed</th><th class=n>Collected</th><th class=n>Still owed</th>
      <th class=n>Bad debt</th><th class=n>Paid above invoice</th><th class=n>Clients owing</th></tr></thead>
      <tbody>{year_rows}</tbody></table></div>
    <p class="small muted">In each year, billed = collected + still owed + bad debt.
      “Paid above invoice” is extra money received on top of that.</p>
    <h2>How old is the money owed?</h2>
    <div class=wrap><table><tbody>{age_rows}</tbody></table></div>
    <h2>Biggest balances</h2>
    <div class=wrap><table><thead><tr><th>Client</th><th class=n>Owes</th>
      <th class=n>Invoices</th><th>Oldest</th></tr></thead><tbody>{top}</tbody></table></div>
    <p><a href="/owes">See everyone who owes →</a></p>"""
    return page("Summary", body, "/")


def owes(conn: sqlite3.Connection, today: date, year: str) -> bytes:
    owing = reports.who_owes(conn, today, year or None)
    ages = reports.age_totals(owing)
    total = sum(c["owed"] for c in owing)
    heads = "".join(f"<th class=n>{label}</th>" for _k, label, *_ in reports.AGE_BUCKETS)
    rows = "".join(
        f"<tr><td>{client_link(c['client'])}</td><td class='n owe'><b>{money(c['owed'])}</b></td>"
        + "".join(
            f"<td class=n>{money(c[k]) if c[k] else '<span class=muted>–</span>'}</td>"
            for k, *_ in reports.AGE_BUCKETS
        )
        + f"<td class=n>{c['invoices']}</td><td class=muted>{escape(c['oldest'][:10])}</td></tr>"
        for c in owing
    )
    foot = (
        f"<tr><td>Total ({len(owing)} clients)</td><td class='n owe'>{money(total)}</td>"
        + "".join(f"<td class=n>{money(ages[k])}</td>" for k, *_ in reports.AGE_BUCKETS)
        + f"<td class=n>{sum(c['invoices'] for c in owing)}</td><td></td></tr>"
    )
    csv_link = "/owes.csv" + (f"?year={quote(year)}" if year else "")
    body = f"""<h1>Who owes{' · invoices from ' + escape(year) if year else ''}</h1>
    {year_picker('/owes', reports.years(conn), year, 'Invoice year')}
    <p class="small muted">Age is counted from the invoice date. Click a client to see their
      invoices and a ready-to-send payment reminder. <a href="{csv_link}">Download this list (CSV)</a></p>
    <div class=wrap><table><thead><tr><th>Client</th><th class=n>Owes</th>{heads}
      <th class=n>Invoices</th><th>Oldest</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=8 class=muted>Nobody owes anything here.</td></tr>'}</tbody>
      <tfoot>{foot}</tfoot></table></div>"""
    return page("Who owes", body, "/owes")


def owes_csv(conn: sqlite3.Connection, today: date, year: str) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Client", "Owes"] + [label for _k, label, *_ in reports.AGE_BUCKETS]
               + ["Invoices", "Oldest invoice"])
    for c in reports.who_owes(conn, today, year or None):
        w.writerow([c["client"], f"{c['owed'] / 100:.2f}"]
                   + [f"{c[k] / 100:.2f}" for k, *_ in reports.AGE_BUCKETS]
                   + [c["invoices"], c["oldest"][:10]])
    return out.getvalue().encode("utf-8-sig")


def paid(conn: sqlite3.Connection, year: str) -> bytes:
    rows_data = reports.who_paid(conn, year or None)
    total = sum(r["paid"] for r in rows_data)
    rows = "".join(
        f"<tr><td>{client_link(r['client'])}</td><td class='n ok'>{money(r['paid'])}</td>"
        f"<td class=n>{r['invoices']}</td><td class=muted>{escape((r['last_paid'] or '')[:10])}</td></tr>"
        for r in rows_data
    )
    body = f"""<h1>Who paid{' in ' + escape(year) if year else ''}</h1>
    {year_picker('/paid', reports.years(conn), year, 'Year paid')}
    <div class=wrap><table><thead><tr><th>Client</th><th class=n>Paid</th>
      <th class=n>Invoices</th><th>Last paid</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=4 class=muted>No payments here.</td></tr>'}</tbody>
      <tfoot><tr><td>Total ({len(rows_data)} clients)</td><td class='n ok'>{money(total)}</td>
      <td></td><td></td></tr></tfoot></table></div>"""
    return page("Who paid", body, "/paid")


def all_clients(conn: sqlite3.Connection, q: str) -> bytes:
    rows = "".join(
        f"<tr><td>{client_link(c['client'])}</td><td class=n>{c['invoices']}</td>"
        f"<td class=n>{money(c['billed'])}</td><td class='n ok'>{money(c['paid'])}</td>"
        f"<td class='n {'owe' if c['owed'] else 'muted'}'>{money(c['owed'])}</td></tr>"
        for c in reports.clients(conn, q)
    )
    body = f"""<h1>All clients</h1>
    <form class=inline method=get action="/clients">
      <input type=search name=q value="{escape(q)}" placeholder="Search by name" autofocus>
      <button>Search</button></form>
    <div class=wrap><table><thead><tr><th>Client</th><th class=n>Invoices</th><th class=n>Billed</th>
      <th class=n>Paid</th><th class=n>Owes</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=5 class=muted>No clients found.</td></tr>'}</tbody></table></div>"""
    return page("All clients", body, "/clients")


STATUS_CLASS = {"paid": "ok", "overdue": "owe", "unpaid": "warn"}


def reminder_text(client: str, open_invoices: list[dict]) -> str:
    lines = [
        f"Dear {client},",
        "",
        "This is a friendly reminder that the following invoices are still open:",
        "",
    ]
    for inv in open_invoices:
        what = ", ".join(li["title"] for li in inv["services"] if li["kind"] == "service")
        lines.append(
            f"  • Invoice {inv['number']} dated {inv['invoice_date'][:10]}: "
            f"{money(inv['owed_cents'])}" + (f" ({what})" if what else "")
        )
    lines += [
        "",
        f"Total due: {money(sum(i['owed_cents'] for i in open_invoices))}",
        "",
        "You can pay through your client portal. Please reply if you have any questions",
        "or if you have already paid.",
        "",
        "Thank you!",
    ]
    return "\n".join(lines)


def client_page(conn: sqlite3.Connection, today: date, name: str) -> bytes:
    invoices = reports.client_invoices(conn, name)
    if not invoices:
        return page("Client", "<h1>Client not found</h1><p><a href='/clients'>All clients</a></p>")
    billed = sum(i["total_cents"] for i in invoices)
    paid_ = sum(i["paid_cents"] for i in invoices)
    owed = sum(i["owed_cents"] for i in invoices)
    written_off = sum(i["written_off_cents"] for i in invoices)
    open_invs = [i for i in invoices if i["owed_cents"] > 0]
    rows = []
    for i in invoices:
        services = "<br>".join(
            f"{escape(li['title'])} <span class=muted>{money(li['total_cents'])}</span>"
            for li in i["services"]
        )
        age = ""
        if i["owed_cents"] > 0:
            label = dict((k, lbl) for k, lbl, *_ in reports.AGE_BUCKETS)[
                reports.age_bucket(i["invoice_date"], today)
            ]
            age = f"<div class='small muted'>{label}</div>"
        rows.append(
            f"<tr><td>{escape(i['number'])}</td><td>{escape(i['invoice_date'][:10])}</td>"
            f"<td><span class='pill {STATUS_CLASS.get(i['status'], '')}'>{escape(i['status'])}</span>{age}</td>"
            f"<td class=small>{services}</td><td class=n>{money(i['total_cents'])}</td>"
            f"<td class='n ok'>{paid_cell(conn, i, name)}</td>"
            f"<td class=n>{owes_cell(i, name, today)}</td></tr>"
        )
    reminder = ""
    if open_invs:
        text = reminder_text(name, open_invs)
        reminder = f"""<h2>Payment reminder</h2>
        <p class="small muted">Ready to paste into an email or text. Edit it as you like.</p>
        <textarea id=reminder rows={text.count(chr(10)) + 2}>{escape(text)}</textarea>
        <p><button type=button onclick="navigator.clipboard.writeText(
          document.getElementById('reminder').value).then(()=>{{this.textContent='Copied'}})">Copy reminder</button></p>"""
    body = f"""<p class=small><a href="/clients">← All clients</a></p>
    <h1>{escape(name)}</h1>
    <div class=cards>
      <div class=card><div class=label>Owes</div><div class="big {'owe' if owed else 'muted'}">{money(owed)}</div>
        <div class="small muted">{len(open_invs)} open invoices</div>
        {f'<div class="small muted">{money(written_off)} written off as bad debt</div>' if written_off else ''}</div>
      <div class=card><div class=label>Paid</div><div class="big ok">{money(paid_)}</div></div>
      <div class=card><div class=label>Billed</div><div class=big>{money(billed)}</div>
        <div class="small muted">{len(invoices)} invoices</div></div>
    </div>
    {reminder}
    <h2>Invoices</h2>
    <div class=wrap><table><thead><tr><th>Invoice</th><th>Date</th><th>Status</th><th>Services</th>
      <th class=n>Total</th><th class=n>Paid</th><th class=n>Owes</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>"""
    return page(name, body)


def bad_debt_button(number: str, mark: bool, back: str) -> str:
    """A small form button that marks or unmarks one invoice as bad debt."""
    if mark:
        label, confirm = "Mark as bad debt", "Write this invoice off as bad debt? You can undo this later."
    else:
        label, confirm = "Undo bad debt", ""
    onsubmit = f' onsubmit="return confirm({escape(repr(confirm))})"' if confirm else ""
    return (
        f'<form class=act method=post action="/bad-debt"{onsubmit}>'
        f'<input type=hidden name=number value="{escape(number)}">'
        f'<input type=hidden name=action value="{"mark" if mark else "unmark"}">'
        f'<input type=hidden name=back value="{escape(back)}">'
        f"<button>{label}</button></form>"
    )


def paid_cell(conn: sqlite3.Connection, inv: dict, client: str) -> str:
    """What was paid, listing any payments entered here with a Delete button each."""
    out = money(inv["paid_cents"])
    if not inv["manual_paid_cents"]:
        return out
    back = "/client?name=" + quote(client)
    for p in db.manual_payments(conn, inv["number"]):
        note = f" · {escape(p['note'])}" if p["note"] else ""
        out += (
            f"<div class='small muted'>{money(p['amount_cents'])} on {escape(p['paid_on'])}{note}"
            f"<form class=act method=post action='/payment' "
            f"onsubmit=\"return confirm('Delete this payment?')\">"
            f"<input type=hidden name=action value=delete>"
            f"<input type=hidden name=id value='{p['id']}'>"
            f"<input type=hidden name=back value='{escape(back)}'><button>Delete</button></form></div>"
        )
    if inv["status"] == "paid":
        out += ("<div class='small warn'>TaxDome now shows this invoice as paid. If that includes "
                "the payment you entered here, delete yours so it isn't counted twice.</div>")
    return out


def payment_form(number: str, back: str, today: date) -> str:
    """A small form to record money received for one invoice, such as a part payment."""
    return (
        "<details class=act><summary class=small>Record payment</summary>"
        "<form method=post action='/payment' class=stack style='margin-top:6px;text-align:left'>"
        "<input type=hidden name=action value=add>"
        f"<input type=hidden name=number value='{escape(number)}'>"
        f"<input type=hidden name=back value='{escape(back)}'>"
        "<div><label class=small>Amount received<br><input name=amount inputmode=decimal required "
        "placeholder='500.00' size=10></label></div>"
        f"<div><label class=small>Date<br><input type=date name=paid_on value='{today.isoformat()}' required></label></div>"
        "<div><label class=small>Note (optional)<br><input name=note placeholder='Zelle' size=14></label></div>"
        "<div><button>Save payment</button></div></form></details>"
    )


def owes_cell(inv: dict, client: str, today: date) -> str:
    back = "/client?name=" + quote(client)
    if inv["bad_debt"]:
        return (
            f"<span class=muted>{money(inv['written_off_cents'])}</span><br>"
            "<span class='pill bad'>bad debt</span>"
            + bad_debt_button(inv["number"], False, back)
        )
    if inv["owed_cents"] > 0:
        return (f"<span class=owe>{money(inv['owed_cents'])}</span>"
                + payment_form(inv["number"], back, today)
                + bad_debt_button(inv["number"], True, back))
    return f"<span class=muted>{money(0)}</span>"


def bad_debts_page(conn: sqlite3.Connection) -> bytes:
    found = reports.bad_debts(conn)
    total = sum(b["written_off_cents"] for b in found)
    by_year: dict[str, int] = {}
    for b in found:
        y = b["invoice_date"][:4] or "No date"
        by_year[y] = by_year.get(y, 0) + b["written_off_cents"]
    rows = "".join(
        f"<tr><td>{escape(b['number'])}</td><td>{client_link(b['client'])}</td>"
        f"<td>{escape(b['invoice_date'][:10])}</td><td class=n>{money(b['total_cents'])}</td>"
        f"<td class='n ok'>{money(b['paid_cents'])}</td>"
        f"<td class='n muted'>{money(b['written_off_cents'])}</td>"
        f"<td class='small muted'>{escape(b['marked_at'][:10])}</td>"
        f"<td>{bad_debt_button(b['number'], False, '/bad-debts')}</td></tr>"
        for b in found
    )
    year_rows = "".join(
        f"<tr><td>{escape(y)}</td><td class=n>{money(c)}</td></tr>"
        for y, c in sorted(by_year.items(), reverse=True)
    )
    body = f"""<h1>Bad debts</h1>
    <p class=muted>Invoices you have written off because they will never be paid. They are left out
      of "owed" everywhere and counted here instead. To write one off, open the client and click
      <b>Mark as bad debt</b> next to the invoice. Your choices are kept when you import a new export.</p>
    <div class=cards><div class=card><div class=label>Written off</div>
      <div class="big muted">{money(total)}</div>
      <div class="small muted">{count(len(found), 'invoice')}</div></div></div>
    {'<h2>By invoice year</h2><div class=wrap><table><tbody>' + year_rows + '</tbody></table></div>' if found else ''}
    <h2>Invoices</h2>
    <div class=wrap><table><thead><tr><th>Invoice</th><th>Client</th><th>Date</th><th class=n>Total</th>
      <th class=n>Paid</th><th class=n>Written off</th><th>Marked on</th><th></th></tr></thead>
      <tbody>{rows or '<tr><td colspan=8 class=muted>No bad debts.</td></tr>'}</tbody></table></div>"""
    return page("Bad debts", body, "/bad-debts")


def year_checkboxes(name: str, years: list[str], chosen: list[str]) -> str:
    return "".join(
        f'<label><input type=checkbox name={name} value="{escape(y)}"'
        f'{" checked" if y in chosen else ""}> {escape(y)}</label>'
        for y in years
    )


def unpaid(conn: sqlite3.Connection, chosen: list[str]) -> bytes:
    all_years = [y for y in reports.years(conn)]
    if not chosen:
        chosen = [y for y in DEFAULT_UNPAID_YEARS if y in all_years] or all_years[1:3]
    chosen = sorted(set(chosen))
    found = reports.unpaid_by_years(conn, chosen)
    heads = "".join(f"<th class=n>{escape(y)}</th>" for y in chosen)
    rows = "".join(
        f"<tr><td>{client_link(c['client'])}</td>"
        + "".join(
            f"<td class=n>{money(c['by_year'][y]) if c['by_year'][y] else '<span class=muted>–</span>'}</td>"
            for y in chosen
        )
        + f"<td class='n owe'><b>{money(c['owed'])}</b></td><td class=n>{c['invoices']}</td>"
        f"<td class=muted>{escape(c['last_paid'][:10]) or 'never'}</td></tr>"
        for c in found
    )
    totals = "".join(f"<td class=n>{money(sum(c['by_year'][y] for c in found))}</td>" for y in chosen)
    query = "&".join(f"year={quote(y)}" for y in chosen)
    body = f"""<h1>Unpaid by year</h1>
    <form method=get action="/unpaid"><fieldset><legend>Invoices from</legend>
      {year_checkboxes('year', all_years, chosen)} <button>Show</button></fieldset></form>
    <p class="small muted">Clients who still owe on invoices dated in {escape(' or '.join(chosen))}.
      Bad debts are left out. Click a client for their invoices and a payment reminder.
      <a href="/unpaid.csv?{query}">Download this list (CSV)</a></p>
    <div class=wrap><table><thead><tr><th>Client</th>{heads}<th class=n>Total unpaid</th>
      <th class=n>Invoices</th><th>Last payment</th></tr></thead>
      <tbody>{rows or f'<tr><td colspan={len(chosen) + 4} class=muted>Nobody owes on invoices from these years.</td></tr>'}</tbody>
      <tfoot><tr><td>Total ({count(len(found), 'client')})</td>{totals}
      <td class='n owe'>{money(sum(c['owed'] for c in found))}</td>
      <td class=n>{sum(c['invoices'] for c in found)}</td><td></td></tr></tfoot></table></div>"""
    return page("Unpaid by year", body, "/unpaid")


def unpaid_csv(conn: sqlite3.Connection, chosen: list[str]) -> bytes:
    chosen = sorted(set(chosen)) or list(DEFAULT_UNPAID_YEARS)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Client"] + [f"Unpaid {y}" for y in chosen] + ["Total unpaid", "Invoices", "Last payment"])
    for c in reports.unpaid_by_years(conn, chosen):
        w.writerow([c["client"]] + [f"{c['by_year'][y] / 100:.2f}" for y in chosen]
                   + [f"{c['owed'] / 100:.2f}", c["invoices"], c["last_paid"][:10]])
    return out.getvalue().encode("utf-8-sig")



def data_page(conn: sqlite3.Connection, db_path: Path, message: str = "") -> bytes:
    here = storage.inspect(db_path)
    old = storage.find_old_data(db_path)
    rows = "".join(
        f"<tr><td class=small>{escape(str(o.path.parent.parent))}</td>"
        f"<td class=n>{o.invoices}</td><td class=n>{o.bad_debts}</td><td class=n>{o.payments}</td>"
        f"<td class='small muted'>{o.modified:%Y-%m-%d %H:%M}</td><td>"
        + (f"<form class=act method=post action='/data/bring-back'>"
           f"<input type=hidden name=path value='{escape(str(o.path))}'>"
           "<button>Bring back bad debts and payments</button></form>" if o.adjustments else "")
        + "</td></tr>"
        for o in old
    )
    old_html = ""
    if old:
        old_html = f"""<h2>Data found in older copies of the app</h2>
    <p class=muted>If bad debts or payments you entered went missing after an update, they are probably
      in one of these. <b>Bring back</b> copies them into your data. Your imported invoices and anything
      already here are kept, so it is safe to click more than once.</p>
    <div class=wrap><table><thead><tr><th>Folder</th><th class=n>Invoices</th><th class=n>Bad debts</th>
      <th class=n>Payments you entered</th><th>Last changed</th><th></th></tr></thead>
      <tbody>{rows}</tbody></table></div>"""
    update = ("<form method=post action='/update' onsubmit=\"this.querySelector('button').textContent="
              "'Updating…'\"><button>Update app</button></form>"
              if storage.can_update() else
              "<p class='small muted'>This copy is a developer checkout. Update it with git.</p>")
    body = f"""<h1>Your data &amp; updates</h1>
    {message}
    <h2>Where your data is kept</h2>
    <p><code>{escape(str(db_path.parent))}</code></p>
    <p class=muted>It stays there when you update the app or download a new copy, so you never need to
      import again or copy folders by hand. Back it up by copying that folder.</p>
    <div class=cards>
      <div class=card><div class=label>Invoices</div><div class=big>{here.invoices if here else 0}</div></div>
      <div class=card><div class=label>Bad debts marked</div><div class=big>{here.bad_debts if here else 0}</div></div>
      <div class=card><div class=label>Payments you entered</div><div class=big>{here.payments if here else 0}</div></div>
    </div>
    <h2>Update the app</h2>
    <p class=muted>Downloads the newest version and puts it in place of this one. Your data is not touched.
      When it says it's done, close the black window and open Fees Tracker again.</p>
    {update}
    {old_html}"""
    return page("Your data & updates", body, "/data")


def import_page(message: str = "") -> bytes:
    body = f"""<h1>Import from TaxDome</h1>
    {message}
    <div class=note>In TaxDome, go to <b>Billing → Invoices</b>, choose <b>Export</b>, and save the CSV file.
      Then choose it below. You can import a new export any time: invoices already here are
      updated, and new ones are added.</div>
    <form method=post action="/import" enctype="multipart/form-data" class=inline>
      <input type=file name=file accept=".csv,text/csv" required>
      <button>Import</button></form>"""
    return page("Import", body, "/import")


def checks(conn: sqlite3.Connection) -> bytes:
    found = reports.problems(conn)
    rows = "".join(
        f"<tr><td>{escape(p['number'])}</td><td>{client_link(p['client'])}</td>"
        f"<td>{escape(p['status'])}</td><td>{escape(p['invoice_date'][:10])}</td>"
        f"<td class='small warn'>{'<br>'.join(escape(x) for x in p['problems'].splitlines())}</td></tr>"
        for p in found
    )
    body = f"""<h1>Data checks</h1>
    <p class="muted">Invoices where the TaxDome export doesn't add up. These don't stop anything
      from working, but they may be worth fixing in TaxDome. Paid invoices always count as
      owing nothing.</p>
    <div class=wrap><table><thead><tr><th>Invoice</th><th>Client</th><th>Status</th><th>Date</th>
      <th>What looks off</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=5 class=muted>Everything adds up.</td></tr>'}</tbody></table></div>"""
    return page("Data checks", body, "/checks")


# ---------------------------------------------------------------- server


def read_upload(content_type: str, body: bytes) -> bytes | None:
    """Return the bytes of the 'file' field from a multipart form body."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    if not msg.is_multipart():
        return None
    for part in msg.iter_parts():
        if part.get_param("name", header="content-disposition") == "file":
            return part.get_payload(decode=True)
    return None


def parse_amount(text: str) -> int | None:
    """'$1,250.50' -> 125050 cents. None if it isn't a number."""
    cleaned = text.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d{1,9}(\.\d{1,2})?|\.\d{1,2}", cleaned):
        return None
    whole, _, frac = cleaned.partition(".")
    return int(whole or 0) * 100 + int((frac + "00")[:2])


def make_handler(db_path: Path, today_fn=date.today, update_fn=storage.update_app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FeesTracker"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, body: bytes, ctype="text/html; charset=utf-8", status=HTTPStatus.OK,
                  extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            multi = parse_qs(url.query)
            q = {k: v[0] for k, v in multi.items()}
            year = q.get("year", "")
            conn = db.connect(db_path)
            try:
                today = today_fn()
                if url.path == "/":
                    self._send(home(conn, today))
                elif url.path == "/owes":
                    self._send(owes(conn, today, year))
                elif url.path == "/owes.csv":
                    name = f"who-owes{'-' + year if year else ''}-{today.isoformat()}.csv"
                    self._send(owes_csv(conn, today, year), "text/csv; charset=utf-8",
                               extra={"Content-Disposition": f'attachment; filename="{name}"'})
                elif url.path == "/unpaid":
                    self._send(unpaid(conn, multi.get("year", [])))
                elif url.path == "/unpaid.csv":
                    self._send(unpaid_csv(conn, multi.get("year", [])), "text/csv; charset=utf-8",
                               extra={"Content-Disposition":
                                      f'attachment; filename="unpaid-{today.isoformat()}.csv"'})
                elif url.path == "/paid":
                    self._send(paid(conn, year))
                elif url.path == "/clients":
                    self._send(all_clients(conn, q.get("q", "")))
                elif url.path == "/client":
                    self._send(client_page(conn, today, q.get("name", "")))
                elif url.path == "/import":
                    self._send(import_page())
                elif url.path == "/data":
                    self._send(data_page(conn, db_path))
                elif url.path == "/checks":
                    self._send(checks(conn))
                elif url.path == "/bad-debts":
                    self._send(bad_debts_page(conn))
                else:
                    self._send(page("Not found", "<h1>Page not found</h1>"), status=HTTPStatus.NOT_FOUND)
            finally:
                conn.close()

        def _same_origin(self) -> bool:
            """Refuse form posts sent by other websites open in the same browser."""
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if not origin:
                return True
            return urlparse(origin).netloc == self.headers.get("Host", "")

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._same_origin():
                self._send(page("Not allowed", "<h1>Not allowed</h1>"), status=HTTPStatus.FORBIDDEN)
                return
            if path == "/bad-debt":
                self._bad_debt()
                return
            if path in ("/data/bring-back", "/update"):
                self._data_action(path)
                return
            if path == "/payment":
                self._payment()
                return
            if path != "/import":
                self._send(page("Not found", "<h1>Page not found</h1>"), status=HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD_BYTES:
                self._send(import_page("<div class='note owe'>That file is too big.</div>"),
                           status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            data = read_upload(self.headers.get("Content-Type", ""), self.rfile.read(length))
            if not data:
                self._send(import_page("<div class='note owe'>Please choose a CSV file.</div>"),
                           status=HTTPStatus.BAD_REQUEST)
                return
            conn = db.connect(db_path)
            try:
                text = data.decode("utf-8-sig", errors="replace")
                counts = db.save_invoices(conn, read_invoices(io.StringIO(text, newline="")), "upload")
            except ImportError_ as exc:
                self._send(import_page(f"<div class='note owe'>{escape(str(exc))}</div>"),
                           status=HTTPStatus.BAD_REQUEST)
                return
            finally:
                conn.close()
            msg = (f"<div class='note ok'>Imported {counts['invoices']} invoices: "
                   f"{counts['added']} new, {counts['updated']} updated. "
                   "<a href='/'>See the summary →</a></div>")
            self._send(import_page(msg))

        def _bad_debt(self):
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            number = form.get("number", "")
            conn = db.connect(db_path)
            try:
                if form.get("action") == "unmark":
                    db.unmark_bad_debt(conn, number)
                elif not db.mark_bad_debt(conn, number, form.get("note", "")):
                    self._send(page("Not found", "<h1>Invoice not found</h1>"),
                               status=HTTPStatus.NOT_FOUND)
                    return
            finally:
                conn.close()
            back = form.get("back", "/")
            if not back.startswith("/") or back.startswith("//"):
                back = "/"
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

        def _data_action(self, path: str):
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            conn = db.connect(db_path)
            try:
                if path == "/update":
                    try:
                        update_fn()
                        msg = ("<div class='note ok'><b>Updated.</b> Close the black Fees Tracker window "
                               "and open Fees Tracker again to use the new version.</div>")
                    except storage.UpdateError as exc:
                        msg = f"<div class='note owe'>{escape(str(exc))}</div>"
                else:
                    # Only files the app itself found can be read, not any path sent to it.
                    allowed = {str(o.path): o for o in storage.find_old_data(db_path)}
                    old = allowed.get(form.get("path", ""))
                    if not old:
                        msg = "<div class='note owe'>That data file was not found.</div>"
                    else:
                        n = storage.bring_back_adjustments(conn, old.path)
                        msg = (f"<div class='note ok'>Brought back {count(n['bad_debts'], 'bad debt')} and "
                               f"{count(n['payments'], 'payment')}. <a href='/'>See the summary →</a></div>")
                self._send(data_page(conn, db_path, msg))
            finally:
                conn.close()

        def _payment(self):
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            back = form.get("back", "/")
            if not back.startswith("/") or back.startswith("//"):
                back = "/"
            conn = db.connect(db_path)
            try:
                if form.get("action") == "delete":
                    if form.get("id", "").isdigit():
                        db.delete_payment(conn, int(form["id"]))
                else:
                    cents = parse_amount(form.get("amount", ""))
                    paid_on = form.get("paid_on", "")
                    try:
                        date.fromisoformat(paid_on)
                    except ValueError:
                        paid_on = ""
                    if cents is None or cents <= 0 or not paid_on:
                        self._send(page("Payment not saved",
                                        "<h1>Payment not saved</h1><p>Please enter an amount such as "
                                        f"500 or 1,250.50 and a date. <a href='{escape(back)}'>Go back</a></p>"),
                                   status=HTTPStatus.BAD_REQUEST)
                        return
                    if not db.add_payment(conn, form.get("number", ""), cents, paid_on, form.get("note", "")):
                        self._send(page("Not found", "<h1>Invoice not found</h1>"),
                                   status=HTTPStatus.NOT_FOUND)
                        return
            finally:
                conn.close()
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

    return Handler


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    db.connect(db_path).close()
    return ThreadingHTTPServer((host, port), make_handler(db_path))
