"""A small web app on your own computer. Nothing leaves the machine.

Uses only the Python standard library, so there is nothing to install.
"""

from __future__ import annotations

import csv
import email.parser
import email.policy
import io
import os
import re
import socket
import sqlite3
from datetime import date
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import __version__, db, reports, storage
from .clientlists import ListError, looks_like_client_list, names_from_text, read_names
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
header { background:var(--card); border-bottom:1px solid var(--line); padding:10px 16px 0 }
header .top, header nav { max-width:1100px; margin:0 auto; display:flex; flex-wrap:wrap; align-items:center }
header .top { gap:4px 14px; margin-bottom:8px }
header b { font-size:18px; margin-right:auto }
header .tools { display:flex; flex-wrap:wrap; gap:4px 14px; font-size:14px }
header .tools span { color:var(--muted) }
header .tools a { color:var(--muted) } header .tools a.on { color:var(--accent); font-weight:600 }
header nav { gap:4px }
header nav a { padding:8px 14px; border-radius:8px 8px 0 0; color:var(--ink); font-weight:550;
               border:1px solid transparent; border-bottom:0; margin-bottom:-1px }
header nav a:hover { background:var(--bg); text-decoration:none }
header nav a.on { background:var(--bg); border-color:var(--line); color:var(--accent) }
a { color:var(--accent); text-decoration:none } a:hover { text-decoration:underline }
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
td.tick, th.tick { width:1%; padding-right:0 }
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
.payform input:not([type=checkbox]) { font:inherit; padding:4px 6px; border:1px solid var(--line);
                    border-radius:6px; background:var(--bg); color:var(--ink) }
.payform input[type=checkbox] { width:18px; height:18px }
.payform .inline { display:flex; flex-wrap:wrap; gap:10px 16px; align-items:center }
.stack > * { margin:0 0 10px }
.actions { position:sticky; top:0; z-index:2; display:flex; flex-wrap:wrap; gap:8px; align-items:center;
           background:var(--bg); padding:8px 0; margin:0 0 6px }
.actions button { padding:5px 12px; font-size:14px }
.actions .group { display:inline-flex; gap:6px; align-items:center; border-left:1px solid var(--line); padding-left:10px }
.actions select { font-size:14px }
.split { display:inline-flex; border:1px solid var(--accent); border-radius:8px; overflow:hidden }
.split button { border-radius:0; background:var(--card); color:var(--accent) }
.split select { border:0; border-left:1px solid var(--accent); background:var(--card); color:var(--accent);
                font-weight:600; padding:0 6px; cursor:pointer }
form.ticked:not(:has(tbody input:checked)) .actions .split { opacity:.45; pointer-events:none }
button.secondary { background:var(--card); color:var(--accent); border:1px solid var(--accent) }
button.link { background:transparent; color:var(--accent); padding:5px 6px }
form.ticked:not(:has(tbody input:checked)) .actions button { opacity:.45; pointer-events:none }
input.filter { font:inherit; padding:7px 10px; border:1px solid var(--line); border-radius:8px; width:min(100%,360px);
               background:var(--card); color:var(--ink); margin:0 0 10px }
"""

# The years asked to follow up on first.
DEFAULT_UNPAID_YEARS = ("2023", "2024")

# The everyday pages, as tabs: what's owed, what came in, who still needs a bill.
NAV = [
    ("/", "Summary"),
    ("/owes", "Who owes"),
    ("/unpaid", "Owed by year"),
    ("/paid", "Who paid"),
    ("/not-billed", "Not billed yet"),
    ("/clients", "All clients"),
]
# Used now and then, so shown small at the top right.
NAV_MORE = [
    ("/import", "Import TaxDome file"),
    ("/bad-debts", "Bad debts"),
    ("/checks", "Data checks"),
    ("/data", "Update app"),
]


def count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def money(cents: int | None) -> str:
    return format_cents(cents or 0)


def page(title: str, body: str, active: str = "") -> bytes:
    def link(href: str, label: str) -> str:
        return f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>'
    tabs = "".join(link(h, l) for h, l in NAV)
    tools = "<span>Tools:</span>" + "".join(link(h, l) for h, l in NAV_MORE)
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} · Fees Tracker</title><style>{STYLE}</style></head>"
        f"<body><header><div class=top><b>Fees Tracker</b><div class=tools>{tools}</div></div>"
        f"<nav>{tabs}</nav></header>"
        f"<main>{body}<p class='small muted' style='margin-top:40px'>Fees Tracker version {__version__}</p>"
        "</main></body></html>"
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
    extra = reports.overpayments(conn)
    extra_totals = reports.overpayment_totals(conn, extra)
    card_fees_by_year: dict[str, int] = {}
    for number, o in extra.items():
        if o["kind"] == "card_fee":
            y = conn.execute("SELECT substr(invoice_date, 1, 4) FROM invoices WHERE number = ?",
                             (number,)).fetchone()[0] or "No date"
            card_fees_by_year[y] = card_fees_by_year.get(y, 0) + o["cents"]
    overpaid = ""
    if extra:
        lines = "".join(
            f"<tr><td>{label}<div class='small muted'>{explain}</div></td>"
            f"<td class=n>{money(extra_totals[key]['cents'])}</td>"
            f"<td class='n muted'>{count(extra_totals[key]['invoices'], 'invoice')}</td></tr>"
            for key, label, explain in reports.OVERPAYMENT_KINDS if extra_totals[key]["invoices"]
        )
        overpaid = (
            "<h2>Paid above the invoice amount</h2>"
            "<p class='small muted'>Not part of the boxes above. Each invoice is listed on "
            "<a href='/checks'>Data checks</a>.</p>"
            f"<div class=wrap><table><tbody>{lines}</tbody></table></div>"
        )
    cards = f"""
    <div class=cards>
      <div class=card><div class=label>Billed</div>
        <div class=big>{money(s.billed_cents)}</div>
        <div class="small muted">{s.invoices} invoices</div></div>
      <div class=card><div class=label>Collected</div>
        <div class="big ok">{money(s.collected_cents)}</div>
        {entered_note(conn)}</div>
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
            <td class="n muted">{money(card_fees_by_year.get(y['year'], 0)) if card_fees_by_year.get(y['year']) else '–'}</td>
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
      <th class=n>Bad debt</th><th class=n>Card fees</th><th class=n>Clients owing</th></tr></thead>
      <tbody>{year_rows}</tbody></table></div>
    <p class="small muted">In each year, billed = collected + still owed + bad debt.
      “Card fees” is what clients paid on top for paying by card.</p>
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
        f"<tr>{tick(c['client'])}<td>{client_link(c['client'])}</td><td class='n owe'><b>{money(c['owed'])}</b></td>"
        + "".join(
            f"<td class=n>{money(c[k]) if c[k] else '<span class=muted>–</span>'}</td>"
            for k, *_ in reports.AGE_BUCKETS
        )
        + f"<td class=n>{c['invoices']}</td><td class=muted>{escape(c['oldest'][:10])}</td></tr>"
        for c in owing
    )
    foot = (
        f"<tr><td></td><td>Total ({len(owing)} clients)</td><td class='n owe'>{money(total)}</td>"
        + "".join(f"<td class=n>{money(ages[k])}</td>" for k, *_ in reports.AGE_BUCKETS)
        + f"<td class=n>{sum(c['invoices'] for c in owing)}</td><td></td></tr>"
    )
    csv_link = "/owes.csv" + (f"?year={quote(year)}" if year else "")
    body = f"""<h1>Who owes{' · invoices from ' + escape(year) if year else ''}</h1>
    {year_picker('/owes', reports.years(conn), year, 'Invoice year')}
    <p class="small muted">Age is counted from the invoice date. Click a client to see their
      invoices and a ready-to-send payment reminder. <a href="{csv_link}">Download this list (CSV)</a></p>
    {FILTER}
    {client_actions(f"""<div class=wrap><table class=list><thead><tr>{TICK_ALL}<th>Client</th><th class=n>Owes</th>{heads}
      <th class=n>Invoices</th><th>Oldest</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=9 class=muted>Nobody owes anything here.</td></tr>'}</tbody>
      <tfoot>{foot}</tfoot></table></div>""", "/owes" + (f"?year={quote(year)}" if year else ""), today,
      money_actions=True)}"""
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


def entered_payments(conn: sqlite3.Connection) -> list[dict]:
    """Payments typed into Fees Tracker (not in TaxDome), newest first."""
    return [dict(r) for r in conn.execute(
        """SELECT m.id, m.invoice_number, m.paid_on, m.amount_cents, m.note, m.entered_at, v.client
           FROM manual_payments m JOIN invoice_view v ON v.number = m.invoice_number
           ORDER BY m.entered_at DESC, m.id DESC""")]


def entered_note(conn: sqlite3.Connection) -> str:
    rows = entered_payments(conn)
    if not rows:
        return ""
    return (f"<div class='small muted'>includes <a href='/entered'>{money(sum(r['amount_cents'] for r in rows))} "
            f"you entered here</a></div>")


def entered_page(conn: sqlite3.Connection) -> bytes:
    rows = entered_payments(conn)
    body_rows = "".join(
        f"<tr><td>{client_link(r['client'])}</td><td>{escape(r['invoice_number'])}</td>"
        f"<td class='n ok'>{money(r['amount_cents'])}</td><td>{escape(r['paid_on'])}</td>"
        f"<td class=muted>{escape(r['entered_at'][:16].replace('T', ' '))}</td>"
        f"<td class='small muted'>{escape(r['note'])}</td></tr>"
        for r in rows)
    body = f"""<h1>Payments you entered</h1>
    <p class=muted>Payments recorded in Fees Tracker with Record payment or the tick boxes. They are
      not in TaxDome, so TaxDome's own totals don't include them. To undo one, open the client.</p>
    <div class=wrap><table><thead><tr><th>Client</th><th>Invoice</th><th class=n>Amount</th>
      <th>Paid on</th><th>Entered</th><th>Note</th></tr></thead>
      <tbody>{body_rows or '<tr><td colspan=6 class=muted>No payments entered yet.</td></tr>'}</tbody>
      <tfoot><tr><td>Total ({count(len(rows), 'payment')})</td><td></td>
      <td class='n ok'>{money(sum(r['amount_cents'] for r in rows))}</td><td colspan=3></td></tr></tfoot>
    </table></div>"""
    return page("Payments you entered", body, "/paid")


def tick(name: str) -> str:
    return f"<td class=tick><input type=checkbox name=name value=\"{escape(name)}\"></td>"


TICK_ALL = ("<th class=tick><input type=checkbox title='Tick all' "
            "onclick=\"for (const b of this.closest('table').querySelectorAll(':scope > tbody > tr > td > input[type=checkbox]'))"
            " b.checked = this.checked\"></th>")


def client_actions(table: str, back: str, today: date, *, year: str = "", money_actions: bool = False) -> str:
    """A table of clients with tick boxes, and one bar of buttons for the ticked clients.

    No pop-ups: every action can be undone (on the client's page, the Bad debts page,
    the Removed clients list or the Didn't file list)."""
    years = tax_year_options(today)
    chosen = year if year in years else str(today.year - 1)
    options = "".join(f"<option{' selected' if y == chosen else ''}>{y}</option>" for y in years)
    buttons = []
    if money_actions:
        buttons.append(
            "<button formaction='/clients/paid' title='Records a payment for everything they owe, dated today'>"
            "Paid in full</button>"
            "<button formaction='/clients/bad-debt' class=secondary title='Writes off everything they owe'>"
            "Bad debt</button>")
    if year:
        buttons.append(
            f"<button formaction='/not-billed/not-filed' class=secondary "
            f"title='Takes them off the {escape(year)} list only'>Didn't file for {escape(year)}</button>")
    buttons.append(
        # The year sits inside the button so it reads as part of the action, not a filter.
        f"<span class=group><span class=split><button formaction='/clients/inactive' "
        f"title='Leaves the Not billed list from this tax year on'>Mark inactive from</button>"
        f"<select name=from_year aria-label='Tax year'>{options}</select></span>"
        "<button formaction='/clients/inactive' name=active value=1 class=link>Make active</button></span>")
    buttons.append("<button class=link title='Hides them from every page and total'>Remove</button>")
    bar = f"""<div class=actions><span class=small><b>Ticked clients:</b></span> {' '.join(buttons)}</div>"""
    return f"""<form method=post action="/clients/remove" class=ticked>
    <input type=hidden name=back value="{escape(back)}">
    {f'<input type=hidden name=year value="{escape(year)}">' if year else ''}
    {bar}
    {table}
    </form>"""


FILTER = ("<input type=search class=filter placeholder='Type a name to find a client' "
          "oninput=\"const q=this.value.toLowerCase();for(const r of document.querySelectorAll('table.list > tbody > tr'))"
          "r.hidden=q&&!r.textContent.toLowerCase().includes(q)\">")


def all_clients(conn: sqlite3.Connection, q: str, message: str = "", today: date | None = None) -> bytes:
    today = today or date.today()
    inactive = db.inactive_clients(conn)
    rows = "".join(
        f"<tr>{tick(c['client'])}<td>{client_link(c['client'])}"
        + (f" <span class='pill muted'>inactive from {escape(inactive[c['client']])}</span>"
           if c["client"] in inactive else "")
        + f"</td><td class=n>{c['invoices']}</td>"
        f"<td class=n>{money(c['billed'])}</td><td class='n ok'>{money(c['paid'])}</td>"
        f"<td class='n {'owe' if c['owed'] else 'muted'}'>{money(c['owed'])}</td></tr>"
        for c in reports.clients(conn, q)
    )
    back = "/clients" + (f"?q={quote(q)}" if q else "")
    table = f"""<div class=wrap><table class=list><thead><tr>{TICK_ALL}<th>Client</th><th class=n>Invoices</th>
      <th class=n>Billed</th><th class=n>Paid</th><th class=n>Owes</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=6 class=muted>No clients found.</td></tr>'}</tbody></table></div>"""
    removed = db.removed_clients(conn)
    removed_html = ""
    if removed:
        items = "".join(f"<tr>{tick(n)}<td>{escape(n)}</td></tr>" for n in removed)
        removed_html = f"""<h2>Removed clients ({len(removed)})</h2>
    <form method=post action="/clients/restore">
    <div class=wrap><table><thead><tr>{TICK_ALL}<th>Client</th></tr></thead><tbody>{items}</tbody></table></div>
    <p><button>Put ticked clients back</button></p></form>"""
    body = f"""<h1>All clients</h1>
    {message}
    <p class="small muted">Tick clients to act on them, or click a name to see their invoices.</p>
    {FILTER}
    {client_actions(table, back, today, money_actions=True)}
    {removed_html}"""
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
    extra = reports.overpayments(conn)
    for i in invoices:
        i["extra"] = extra.get(i["number"])
    paid_ = sum(i["paid_cents"] - (i["extra"]["cents"] if i["extra"] and i["extra"]["kind"] == "shared" else 0)
                for i in invoices)
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
    <h1>{escape(name)}{inactive_badge(conn, name)}</h1>
    <div class=cards>
      <div class=card><div class=label>Owes</div><div class="big {'owe' if owed else 'muted'}">{money(owed)}</div>
        <div class="small muted">{len(open_invs)} open invoices</div>
        {f'<div class="small muted">{money(written_off)} written off as bad debt</div>' if written_off else ''}</div>
      <div class=card><div class=label>Paid</div><div class="big ok">{money(paid_)}</div></div>
      <div class=card><div class=label>Billed</div><div class=big>{money(billed)}</div>
        <div class="small muted">{len(invoices)} invoices</div></div>
    </div>
    {payments_form(open_invs, '/client?name=' + quote(name), today) if open_invs else ''}
    {reminder}
    <h2>Invoices</h2>
    <div class=wrap><table><thead><tr><th>Invoice</th><th>Date</th><th>Status</th><th>Services</th>
      <th class=n>Total</th><th class=n>Paid</th><th class=n>Owes</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>
    <h2>More</h2>
    {inactive_box(conn, name, today)}"""
    return page(name, body)


def inactive_badge(conn: sqlite3.Connection, name: str) -> str:
    since = db.inactive_clients(conn).get(name)
    return f" <span class='pill muted small'>inactive from {escape(since)}</span>" if since else ""


def inactive_box(conn: sqlite3.Connection, name: str, today: date) -> str:
    """Shows whether a client is inactive, with a button to change it."""
    back = "/client?name=" + quote(name)
    since = db.inactive_clients(conn).get(name)
    hidden = (f"<input type=hidden name=name value=\"{escape(name)}\">"
              f"<input type=hidden name=back value=\"{escape(back)}\">")
    if since:
        return (f"<div class=note><b>No longer your client from tax year {escape(since)}.</b> Don't bill them "
                f"for {escape(since)} or later. Their invoices below stay as they are. "
                f"<form class=act method=post action='/clients/inactive'>{hidden}"
                f"<button name=from_year value=''>Make active again</button></form></div>")
    options = "".join(f"<option{' selected' if y == str(today.year - 1) else ''}>{y}</option>"
                      for y in tax_year_options(today))
    return (f"<form class=act method=post action='/clients/inactive'>{hidden}"
            f"<span class='small muted'>No longer your client? </span>"
            f"<select name=from_year>{options}</select> <button>Inactive from this tax year</button></form>")


def bad_debt_button(number: str, mark: bool, back: str) -> str:
    """A small form button that marks or unmarks one invoice as bad debt."""
    label = "Mark as bad debt" if mark else "Undo bad debt"
    return (
        f'<form class=act method=post action="/bad-debt">'
        f'<input type=hidden name=number value="{escape(number)}">'
        f'<input type=hidden name=action value="{"mark" if mark else "unmark"}">'
        f'<input type=hidden name=back value="{escape(back)}">'
        f"<button>{label}</button></form>"
    )


def paid_cell(conn: sqlite3.Connection, inv: dict, client: str) -> str:
    """What was paid, listing any payments entered here with a Delete button each."""
    out = money(inv["paid_cents"])
    extra = inv.get("extra")
    if extra:
        label = {k: lbl for k, lbl, _ in reports.OVERPAYMENT_KINDS}[extra["kind"]]
        out += f"<div class='small muted'>incl. {money(extra['cents'])}: {label.lower()}</div>"
    if not inv["manual_paid_cents"]:
        return out
    back = "/client?name=" + quote(client)
    for p in db.manual_payments(conn, inv["number"]):
        note = f" · {escape(p['note'])}" if p["note"] else ""
        out += (
            f"<div class='small muted'>{money(p['amount_cents'])} on {escape(p['paid_on'])}{note}"
            f"<form class=act method=post action='/payment'>"
            f"<input type=hidden name=action value=delete>"
            f"<input type=hidden name=id value='{p['id']}'>"
            f"<input type=hidden name=back value='{escape(back)}'><button>Delete</button></form></div>"
        )
    if inv["status"] == "paid":
        out += ("<div class='small warn'>TaxDome now shows this invoice as paid. If that includes "
                "the payment you entered here, delete yours so it isn't counted twice.</div>")
    return out


def payments_form(open_invoices: list[dict], back: str, today: date) -> str:
    """Tick the invoices a client paid. Ticking fills in what is still owed, which you
    can change for a part payment. One date and note go with all of them."""
    rows = "".join(
        f"<tr><td><input type=checkbox name=pay value='{escape(i['number'])}' "
        f"id='pay-{escape(i['number'])}' data-owed='{i['owed_cents'] / 100:.2f}' onchange='fillAmount(this)'></td>"
        f"<td><label for='pay-{escape(i['number'])}'>{escape(i['number'])}</label></td>"
        f"<td>{escape(i['invoice_date'][:10])}</td><td class='n owe'>{money(i['owed_cents'])}</td>"
        f"<td class=n><input name='amount_{escape(i['number'])}' inputmode=decimal size=10 "
        f"placeholder='0.00' oninput='tickIfFilled(this)'></td></tr>"
        for i in open_invoices
    )
    return f"""<h2>Record a payment</h2>
    <p class="small muted">Tick the invoices the client paid. The amount still owed is filled in for you;
      change it if they paid only part. The rest stays owed.</p>
    <form method=post action="/payment" class=payform>
      <input type=hidden name=action value=add><input type=hidden name=back value="{escape(back)}">
      <div class=wrap><table><thead><tr><th></th><th>Invoice</th><th>Date</th><th class=n>Owes</th>
        <th class=n>Amount received</th></tr></thead><tbody>{rows}</tbody></table></div>
      <div class=inline style="margin-top:10px">
        <label>Date paid <input type=date name=paid_on value="{today.isoformat()}" required></label>
        <label>Note <input name=note placeholder="Zelle, cheque…" size=16></label>
        <button>Save payment</button></div>
    </form>
    <script>
    function fillAmount(box) {{
      const amount = box.form.elements['amount_' + box.value];
      amount.value = box.checked ? box.dataset.owed : '';
      if (box.checked) amount.select();
    }}
    function tickIfFilled(input) {{
      const box = document.getElementById('pay-' + input.name.slice(7));
      box.checked = input.value.trim() !== '';
    }}
    </script>"""


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


def unpaid(conn: sqlite3.Connection, chosen: list[str], today: date | None = None) -> bytes:
    today = today or date.today()
    all_years = [y for y in reports.years(conn)]
    if not chosen:
        chosen = [y for y in DEFAULT_UNPAID_YEARS if y in all_years] or all_years[1:3]
    chosen = sorted(set(chosen))
    found = reports.unpaid_by_years(conn, chosen)
    heads = "".join(f"<th class=n>{escape(y)}</th>" for y in chosen)
    rows = "".join(
        f"<tr>{tick(c['client'])}<td>{client_link(c['client'])}</td>"
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
    body = f"""<h1>Owed by year</h1>
    <form method=get action="/unpaid"><fieldset><legend>Invoices from</legend>
      {year_checkboxes('year', all_years, chosen)} <button>Show</button></fieldset></form>
    <p class="small muted">Clients who still owe on invoices dated in {escape(' or '.join(chosen))}.
      Bad debts are left out. Click a client for their invoices and a payment reminder.
      <a href="/unpaid.csv?{query}">Download this list (CSV)</a></p>
    {FILTER}
    {client_actions(f"""<div class=wrap><table class=list><thead><tr>{TICK_ALL}<th>Client</th>{heads}<th class=n>Total unpaid</th>
      <th class=n>Invoices</th><th>Last payment</th></tr></thead>
      <tbody>{rows or f'<tr><td colspan={len(chosen) + 5} class=muted>Nobody owes on invoices from these years.</td></tr>'}</tbody>
      <tfoot><tr><td></td><td>Total ({count(len(found), 'client')})</td>{totals}
      <td class='n owe'>{money(sum(c['owed'] for c in found))}</td>
      <td class=n>{sum(c['invoices'] for c in found)}</td><td></td></tr></tfoot></table></div>""",
      "/unpaid?" + query, today, money_actions=True)}"""
    return page("Owed by year", body, "/unpaid")


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


KIND_NOTE = {
    "in_taxdome": "In TaxDome, but no invoice for this tax year",
    "maybe": "Not sure of the TaxDome name",
    "never": "No invoices in TaxDome at all",
}


def tax_year_options(today: date) -> list[str]:
    return [str(y) for y in range(today.year, today.year - 9, -1)]


def not_billed_page(conn: sqlite3.Connection, today: date, year: str, message: str = "") -> bytes:
    lists = db.client_lists(conn)
    years = [l["year"] for l in lists]
    if year not in years:
        year = years[0] if years else ""
    tabs = " ".join(
        f"<a href='/not-billed?year={quote(y)}' class='pill' style='{'font-weight:650' if y == year else ''}'>"
        f"{escape(y)}</a>" for y in years)
    table = ""
    if year:
        nb = reports.not_billed(conn, year)
        counts = {k: sum(1 for r in nb["rows"] if r["kind"] == k) for k in KIND_NOTE}
        def row(r: dict) -> str:
            same = ""
            if r["maybe"]:
                pair = escape(r['name'] + chr(9) + r['maybe'])
                same = (f" <button class=small formaction='/not-billed/match' name=match "
                        f"value=\"{pair}\">Same client</button>"
                        f" <button class='small secondary' formaction='/not-billed/not-match' name=match "
                        f"value=\"{pair}\">Not the same</button>")
            return (
                f"<tr>{tick(r['client'] or r['name'])}<td>{client_link(r['client']) if r['client'] else escape(r['name'])}"
                + (f"<span hidden class=alias>{escape(r['name'])}</span>"
                   if r["client"] and r["client"].lower() != r["name"].lower() else "")
                + f"</td><td class=small>{KIND_NOTE[r['kind']]}"
                + (f": maybe {client_link(r['maybe'])}{same}" if r["maybe"] else "")
                + f"</td><td class=muted>{escape(', '.join(r['tax_years'])) or '–'}</td>"
                f"<td class=muted>{escape(r['last_invoice']) or '–'}</td>"
                f"<td class='n {'owe' if r['owed'] else 'muted'}'>{money(r['owed'])}</td></tr>")

        rows = "".join(row(r) for r in nb["rows"] if r["kind"] != "never")
        never = [r for r in nb["rows"] if r["kind"] == "never"]
        if never:
            rows += (f"<tr><td colspan=6><details><summary><b>{count(len(never), 'client')} with no invoices in "
                     "TaxDome at all</b> <span class='small muted'>(maybe never billed in TaxDome, or billed "
                     "another way). Click to show.</span></summary><table><tbody>"
                     + "".join(row(r) for r in never) + "</tbody></table></details></td></tr>")
        table = f"""
    <div class=cards>
      <div class=card><div class=label>Not billed for {escape(year)}</div>
        <div class="big owe">{len(nb['rows'])}</div>
        <div class="small muted">of {count(nb['listed'], 'client')} on your {escape(year)} list</div></div>
      <div class=card><div class=label>In TaxDome, no {escape(year)} invoice</div>
        <div class=big>{counts['in_taxdome']}</div></div>
      <div class=card><div class=label>Not sure of TaxDome name</div>
        <div class="big warn">{counts['maybe']}</div></div>
      <div class=card><div class=label>No invoices in TaxDome</div>
        <div class="big muted">{counts['never']}</div></div>
    </div>
    <p class="small muted">An invoice counts for the tax year written on it (“Tax year {escape(year)}”,
      “TY {escape(year)}”, “{escape(year)} tax return”). If none is written, it counts for the year before
      the invoice date, because {escape(year)} returns are usually billed in {int(year) + 1}.
      <a href="/not-billed.csv?year={quote(year)}">Download this list (CSV)</a></p>
    {client_actions(f"""<div class=wrap><table class=list><thead><tr>{TICK_ALL}<th>Client</th><th>Why it's here</th>
      <th>Tax years billed</th><th>Last invoice</th><th class=n>Owes now</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=6 class=muted>Everyone on this list was billed.</td></tr>'}</tbody>
    </table></div>""", "/not-billed?year=" + quote(year), today, year=year)}
    {not_filed_html(conn, year)}"""
    options = "".join(f"<option{' selected' if y == str(today.year - 1) else ''}>{y}</option>"
                      for y in tax_year_options(today))
    upload = f"""<h2>Add or replace a client list</h2>
    <form method=post action="/not-billed" enctype="multipart/form-data" class="payform">
      <p><label>Tax year <select name=year>{options}</select></label></p>
      <p><label>Client list from your tax software (Excel .xlsx or CSV): <input type=file name=file
        accept=".csv,.xlsx,.xlsm,.txt"></label></p>
      <p><label>or paste names, one per line:<br><textarea name=names rows=3></textarea></label></p>
      <p><button>Save list</button> <span class="small muted">Only client names are kept. SSNs, EINs,
        emails and addresses in the file are never saved.</span></p>
      <p class="small muted">Client lists are kept on their own: saving one never changes your clients,
        invoices, payments or bad debts, and saving a year again only replaces that year's list.</p>
    </form>"""
    body = f"""<h1>Not billed yet{' for tax year ' + escape(year) if year else ''}</h1>
    {message}
    <p class=muted>Clients on your client list for a tax year who have no TaxDome invoice for that year.</p>
    {('<p>Tax year: ' + tabs + '</p>') if years else '<div class=note>Add your client list for a tax year below to see who wasn’t billed.</div>'}
    {table}
    {upload}"""
    return page("Not billed", body, "/not-billed")


def not_filed_html(conn: sqlite3.Connection, year: str) -> str:
    names = db.not_filed(conn, year)
    if not names:
        return ""
    items = "".join(f"<tr>{tick(n)}<td>{escape(n)}</td></tr>" for n in names)
    return f"""<h2>Didn't file for {escape(year)} ({len(names)})</h2>
    <form method=post action="/not-billed/not-filed">
    <input type=hidden name=year value="{escape(year)}"><input type=hidden name=filed value=1>
    <input type=hidden name=back value="/not-billed?year={quote(year)}">
    <div class=wrap><table><thead><tr>{TICK_ALL}<th>Client</th></tr></thead><tbody>{items}</tbody></table></div>
    <p><button>Put ticked clients back on the list</button></p></form>"""


def not_billed_csv(conn: sqlite3.Connection, year: str) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Client", "Name in TaxDome", "Why it's here", "Tax years billed", "Last invoice", "Owes now"])
    for r in reports.not_billed(conn, year)["rows"]:
        w.writerow([r["name"], r["client"] or r["maybe"] or "", KIND_NOTE[r["kind"]],
                    ", ".join(r["tax_years"]), r["last_invoice"], f"{r['owed'] / 100:.2f}"])
    return out.getvalue().encode("utf-8-sig")


def guess_tax_year(filename: str, today: date) -> str:
    """The tax year in a file name such as "Clients 2024.csv", else last year."""
    found = [y for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", filename) if y in tax_year_options(today)]
    return found[-1] if found else str(today.year - 1)


def client_list_found(filename: str, names: list[str], today: date) -> bytes:
    """Import was given a client list: offer to save it as a tax year's client list."""
    year = guess_tax_year(filename, today)
    options = "".join(f"<option{' selected' if y == year else ''}>{y}</option>"
                      for y in tax_year_options(today))
    body = f"""<h1>This is a client list</h1>
    <div class=note>{escape(filename) or 'This file'} is a client list from your tax software, not a
      TaxDome bills export, so it was not imported as bills. Your invoices were not changed.</div>
    <p>It has {count(len(names), 'client')}. Save it as the client list for a tax year to see who wasn't
      billed. Saving it never changes your clients, invoices, payments or bad debts, and only replaces
      that year's list.</p>
    <form method=post action="/not-billed" enctype="multipart/form-data" class="payform">
      <textarea name=names hidden>{escape(chr(10).join(names))}</textarea>
      <input type=hidden name=source value="{escape(filename)}">
      <p><label>Tax year <select name=year>{options}</select></label>
        <button>Save as client list</button></p>
    </form>
    <p class="small muted">Only client names are kept. To load your bills, export them in TaxDome from
      Billing → Invoices and <a href="/import">import that file</a>.</p>"""
    return page("Client list", body, "/not-billed")


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
    extra = reports.overpayments(conn)
    labels = {k: label for k, label, _ in reports.OVERPAYMENT_KINDS}

    def what(p: dict) -> str:
        lines = [escape(x) for x in p["problems"].splitlines()]
        o = extra.get(p["number"])
        if o:
            css = "muted" if o["kind"] != "other" else "warn"
            lines.append(f"<span class={css}><b>{labels[o['kind']]}</b>: {money(o['cents'])} "
                         "above the invoice amount</span>")
        return "<br>".join(lines)

    rows = "".join(
        f"<tr><td>{escape(p['number'])}</td><td>{client_link(p['client'])}</td>"
        f"<td>{escape(p['status'])}</td><td>{escape(p['invoice_date'][:10])}</td>"
        f"<td class='small warn'>{what(p)}</td></tr>"
        for p in found
    )
    totals = reports.overpayment_totals(conn, extra)
    summary = "".join(
        f"<tr><td>{label}<div class='small muted'>{explain}</div></td>"
        f"<td class=n>{money(totals[k]['cents'])}</td><td class='n muted'>{count(totals[k]['invoices'], 'invoice')}</td></tr>"
        for k, label, explain in reports.OVERPAYMENT_KINDS if totals[k]["invoices"]
    )
    body = f"""<h1>Data checks</h1>
    <p class="muted">Invoices where the TaxDome export doesn't add up. These don't stop anything
      from working, but they may be worth fixing in TaxDome. Paid invoices always count as
      owing nothing.</p>
    {'<h2>Paid above the invoice amount</h2><div class=wrap><table><tbody>' + summary + '</tbody></table></div><h2>Invoices</h2>' if summary else ''}
    <div class=wrap><table><thead><tr><th>Invoice</th><th>Client</th><th>Status</th><th>Date</th>
      <th>What looks off</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=5 class=muted>Everything adds up.</td></tr>'}</tbody></table></div>"""
    return page("Data checks", body, "/checks")


# ---------------------------------------------------------------- server


def read_form(content_type: str, body: bytes) -> dict[str, tuple[str, bytes]]:
    """The fields of a multipart form body, as {name: (filename, bytes)}."""
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    if not msg.is_multipart():
        return {}
    fields = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            fields[name] = (part.get_filename() or "", part.get_payload(decode=True) or b"")
    return fields


def read_upload(content_type: str, body: bytes) -> bytes | None:
    """Return the bytes of the 'file' field from a multipart form body."""
    return read_form(content_type, body).get("file", ("", b""))[1] or None


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
                    self._send(unpaid(conn, multi.get("year", []), today))
                elif url.path == "/unpaid.csv":
                    self._send(unpaid_csv(conn, multi.get("year", [])), "text/csv; charset=utf-8",
                               extra={"Content-Disposition":
                                      f'attachment; filename="unpaid-{today.isoformat()}.csv"'})
                elif url.path == "/entered":
                    self._send(entered_page(conn))
                elif url.path == "/paid":
                    self._send(paid(conn, year))
                elif url.path == "/clients":
                    self._send(all_clients(conn, q.get("q", ""), today=today))
                elif url.path == "/client":
                    self._send(client_page(conn, today, q.get("name", "")))
                elif url.path == "/import":
                    self._send(import_page())
                elif url.path == "/not-billed":
                    self._send(not_billed_page(conn, today, q.get("year", "")))
                elif url.path == "/not-billed.csv":
                    self._send(not_billed_csv(conn, q.get("year", "")), "text/csv; charset=utf-8",
                               extra={"Content-Disposition":
                                      f'attachment; filename="not-billed-{quote(q.get("year", ""))}.csv"'})
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
            if path == "/not-billed":
                self._save_list()
                return
            if path in ("/clients/paid", "/clients/bad-debt"):
                self._settle(path)
                return
            if path == "/clients/inactive":
                self._inactive()
                return
            if path == "/not-billed/not-filed":
                self._not_filed()
                return
            if path in ("/not-billed/match", "/not-billed/not-match"):
                self._confirm_match(same=path == "/not-billed/match")
                return
            if path in ("/clients/remove", "/clients/restore"):
                self._remove_clients(path)
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
            filename, data = read_form(self.headers.get("Content-Type", ""),
                                       self.rfile.read(length)).get("file", ("", b""))
            if not data:
                self._send(import_page("<div class='note owe'>Please choose a CSV file.</div>"),
                           status=HTTPStatus.BAD_REQUEST)
                return
            if looks_like_client_list(data, filename):
                # A client list sent to Import by mistake: offer to save it as one instead.
                # Nothing is saved until the tax year is chosen, and bills are never touched.
                try:
                    self._send(client_list_found(filename, read_names(data, filename), today_fn()))
                    return
                except ListError:
                    pass
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

        def _settle(self, path: str):
            """Paid in full, or written off as bad debt, for everything the ticked clients owe."""
            length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            back = (fields.get("back") or ["/owes"])[0]
            if not back.startswith("/") or back.startswith("//"):
                back = "/owes"
            names = set(fields.get("name", []))
            conn = db.connect(db_path)
            try:
                today = today_fn().isoformat()
                for name in names:
                    for inv in reports.client_invoices(conn, name):
                        if inv["owed_cents"] <= 0:
                            continue
                        if path == "/clients/paid":
                            db.add_payment(conn, inv["number"], inv["owed_cents"], today, "Marked paid in full")
                        else:
                            db.mark_bad_debt(conn, inv["number"], "Marked from a client list")
            finally:
                conn.close()
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

        def _inactive(self):
            length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            year = "" if fields.get("active") else (fields.get("from_year") or [""])[0]
            back = (fields.get("back") or ["/clients"])[0]
            if not back.startswith("/") or back.startswith("//"):
                back = "/clients"
            if year and year not in tax_year_options(today_fn()):
                self._send(page("Not saved", "<h1>Please choose a tax year</h1>"), status=HTTPStatus.BAD_REQUEST)
                return
            if fields.get("name"):
                conn = db.connect(db_path)
                try:
                    db.set_inactive(conn, [n for n in fields["name"] if n], year or None)
                finally:
                    conn.close()
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

        def _not_filed(self):
            length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            year = (fields.get("year") or [""])[0]
            back = "/not-billed?year=" + quote(year)
            if year and fields.get("name"):
                conn = db.connect(db_path)
                try:
                    db.mark_not_filed(conn, year, fields["name"], filed=bool(fields.get("filed")))
                finally:
                    conn.close()
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

        def _confirm_match(self, same: bool = True):
            length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            name, _, client = (fields.get("match") or [""])[0].partition("\t")
            back = (fields.get("back") or ["/not-billed"])[0]
            if not back.startswith("/") or back.startswith("//"):
                back = "/not-billed"
            if name and client:
                conn = db.connect(db_path)
                try:
                    (db.confirm_match if same else db.reject_match)(conn, name, client)
                finally:
                    conn.close()
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

        def _remove_clients(self, path: str):
            length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            names = fields.get("name", [])
            back = (fields.get("back") or ["/clients"])[0]
            if not back.startswith("/") or back.startswith("//"):
                back = "/clients"
            conn = db.connect(db_path)
            try:
                if path == "/clients/remove":
                    db.remove_clients(conn, names)
                else:
                    db.restore_clients(conn, names)
            finally:
                conn.close()
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

        def _save_list(self):
            length = int(self.headers.get("Content-Length") or 0)
            today = today_fn()
            conn = db.connect(db_path)
            try:
                def reply(text: str, ok: bool, year: str = "", status=HTTPStatus.OK):
                    css = "ok" if ok else "owe"
                    self._send(not_billed_page(conn, today, year, f"<div class='note {css}'>{text}</div>"),
                               status=status)

                if length > MAX_UPLOAD_BYTES:
                    return reply("That file is too big.", False, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                form = read_form(self.headers.get("Content-Type", ""), self.rfile.read(length))
                year = form.get("year", ("", b""))[1].decode("utf-8", "replace").strip()
                if year not in tax_year_options(today):
                    return reply("Please choose a tax year.", False, status=HTTPStatus.BAD_REQUEST)
                filename, data = form.get("file", ("", b""))
                pasted = form.get("names", ("", b""))[1].decode("utf-8", "replace")
                try:
                    if data:
                        names, source = read_names(data, filename), filename
                    elif pasted.strip():
                        source = form.get("source", ("", b""))[1].decode("utf-8", "replace").strip()
                        names, source = names_from_text(pasted), source or "pasted names"
                    else:
                        return reply("Please choose a file or paste some names.", False,
                                     status=HTTPStatus.BAD_REQUEST)
                except ListError as exc:
                    return reply(escape(str(exc)), False, status=HTTPStatus.BAD_REQUEST)
                db.save_client_list(conn, year, names, source)
                reply(f"Saved your {escape(year)} list with {count(len(names), 'client')}.", True, year)
            finally:
                conn.close()

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
            length = min(int(self.headers.get("Content-Length") or 0), 100_000)
            fields = parse_qs(self.rfile.read(length).decode("utf-8"))
            form = {k: v[0] for k, v in fields.items()}
            back = form.get("back", "/")
            if not back.startswith("/") or back.startswith("//"):
                back = "/"

            def not_saved(text: str, status=HTTPStatus.BAD_REQUEST):
                self._send(page("Payment not saved", f"<h1>Payment not saved</h1><p>{text} "
                                f"<a href='{escape(back)}'>Go back</a></p>"), status=status)

            conn = db.connect(db_path)
            try:
                if form.get("action") == "delete":
                    if form.get("id", "").isdigit():
                        db.delete_payment(conn, int(form["id"]))
                else:
                    paid_on = form.get("paid_on", "")
                    try:
                        date.fromisoformat(paid_on)
                    except ValueError:
                        return not_saved("Please choose the date it was paid.")
                    # Ticked invoices, each with its own amount; or one invoice and amount.
                    numbers = fields.get("pay") or ([form["number"]] if form.get("number") else [])
                    if not numbers:
                        return not_saved("Please tick at least one invoice.")
                    to_save = []
                    for number in dict.fromkeys(numbers):
                        raw = form.get(f"amount_{number}", form.get("amount", "") if "pay" not in fields else "")
                        cents = parse_amount(raw)
                        if not cents:
                            return not_saved(f"Please enter an amount such as 500 or 1,250.50 for invoice "
                                             f"{escape(number)}. Nothing was saved.")
                        if not conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
                            return not_saved("Invoice not found. Nothing was saved.", HTTPStatus.NOT_FOUND)
                        to_save.append((number, cents))
                    for number, cents in to_save:
                        db.add_payment(conn, number, cents, paid_on, form.get("note", ""))
            finally:
                conn.close()
            self._send(b"", status=HTTPStatus.SEE_OTHER, extra={"Location": back})

    return Handler


class _Server(ThreadingHTTPServer):
    # On Windows, address reuse lets a second copy share a port that an older copy,
    # still running, keeps answering on. Never share: take a free port instead.
    allow_reuse_address = os.name != "nt"


def _in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Start the app on `port`, or on the next free port if another copy is already running."""
    db.connect(db_path).close()
    for p in range(port, port + 20):
        if _in_use(host, p):
            continue
        try:
            return _Server((host, p), make_handler(db_path))
        except OSError:
            continue
    return _Server((host, 0), make_handler(db_path))
