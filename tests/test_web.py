import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from feestracker import db, web

from .conftest import FIXTURE, TODAY


@pytest.fixture
def server(tmp_path):
    path = tmp_path / "web.db"
    db.connect(path).close()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(path, lambda: TODAY))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8-sig")


def upload(base, data: bytes, filename="bills.csv"):
    boundary = "----feestrackertest"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        "Content-Type: text/csv\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        base + "/import", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_empty_app_points_to_import(server):
    assert "Go to <a href=\"/import\">Import</a>" in get(server + "/")


def test_upload_then_browse(server):
    status, html = upload(server, FIXTURE.read_bytes())
    assert status == 200 and "Imported 6 invoices: 6 new, 0 updated" in html

    home = get(server + "/")
    assert "$1,556.00" in home  # owed now
    assert "Over 1 year" in home

    owes = get(server + "/owes?year=2022")
    assert "Rivera Maria" in owes and "Acme Bakery" not in owes

    assert "Chen &amp; Sons Inc" in get(server + "/paid?year=2026")

    client = get(server + "/client?name=Rivera%20Maria")
    assert "Total due: $750.00" in client
    assert "Invoice 1003 dated 2022-05-10: $600.00" in client

    csv_text = get(server + "/owes.csv")
    assert csv_text.splitlines()[1].startswith('"Acme Bakery, LLC",806.00')

    assert "nothing is left to pay" in get(server + "/checks")


def test_upload_wrong_file(server):
    status, html = upload(server, b"Name,Amount\nBob,1\n")
    assert status == 400 and "Missing columns" in html


def test_html_is_escaped(server, tmp_path):
    evil = FIXTURE.read_text().replace("Rivera Maria", "<script>x</script>")
    upload(server, evil.encode())
    html = get(server + "/owes")
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


def post_form(base, path, fields, headers=None):
    from urllib.parse import urlencode

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    req = urllib.request.Request(
        base + path, data=urlencode(fields).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(req) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers


def test_mark_and_undo_bad_debt(server):
    upload(server, FIXTURE.read_bytes())
    assert "Mark as bad debt" in get(server + "/client?name=Rivera%20Maria")

    status, headers = post_form(server, "/bad-debt", {
        "number": "1003", "action": "mark", "back": "/client?name=Rivera%20Maria"})
    assert status == 303 and headers["Location"] == "/client?name=Rivera%20Maria"

    client = get(server + "/client?name=Rivera%20Maria")
    assert "Undo bad debt" in client
    assert "Total due: $150.00" in client  # the reminder leaves out the bad debt
    assert "Invoice 1003" not in client.split("</textarea>")[0]

    home = get(server + "/")
    assert "$956.00" in home  # owed now
    assert "Written off as bad debt" in home and "$600.00" in home
    assert "1003" in get(server + "/bad-debts")

    post_form(server, "/bad-debt", {"number": "1003", "action": "unmark", "back": "/bad-debts"})
    assert "No bad debts." in get(server + "/bad-debts")
    assert "$1,556.00" in get(server + "/")


def test_bad_debt_rejects_other_sites_and_bad_redirects(server):
    upload(server, FIXTURE.read_bytes())
    status, _ = post_form(server, "/bad-debt", {"number": "1003", "action": "mark"},
                          {"Origin": "https://evil.example"})
    assert status == 403
    assert "No bad debts." in get(server + "/bad-debts")

    status, headers = post_form(server, "/bad-debt", {
        "number": "1003", "action": "mark", "back": "//evil.example/"})
    assert status == 303 and headers["Location"] == "/"
    status, _ = post_form(server, "/bad-debt", {"number": "missing", "action": "mark"})
    assert status == 404


def test_unpaid_by_year(server):
    upload(server, FIXTURE.read_bytes())
    html = get(server + "/unpaid?year=2022&year=2026")
    assert "Rivera Maria" in html and "$750.00" in html
    assert get(server + "/unpaid.csv?year=2022").splitlines()[1].startswith("Rivera Maria,600.00")
    assert "Nobody owes" in get(server + "/unpaid")  # 2023 and 2024 by default


def test_parse_amount():
    assert web.parse_amount("$1,250.5") == 125050
    assert web.parse_amount("500") == 50000
    assert web.parse_amount(".75") == 75
    for bad in ["", "abc", "1.234", "-5", "inf", "nan", "1e5"]:
        assert web.parse_amount(bad) is None


def test_record_part_payment(server):
    upload(server, FIXTURE.read_bytes())
    back = "/client?name=Rivera%20Maria"
    assert "Record payment" in get(server + back)

    status, headers = post_form(server, "/payment", {
        "action": "add", "number": "1003", "amount": "$200.00", "paid_on": "2026-11-15",
        "note": "Zelle", "back": back})
    assert status == 303 and headers["Location"] == back

    client = get(server + back)
    assert "$200.00 on 2026-11-15 · Zelle" in client
    assert "Invoice 1003 dated 2022-05-10: $400.00" in client  # the rest is still owed
    assert "Total due: $550.00" in client
    assert "$1,356.00" in get(server + "/")  # owed now, was $1,556.00

    status, _ = post_form(server, "/payment", {
        "action": "add", "number": "1003", "amount": "lots", "paid_on": "2026-11-15", "back": back})
    assert status == 400
    status, _ = post_form(server, "/payment", {
        "action": "add", "number": "nope", "amount": "5", "paid_on": "2026-11-15", "back": back})
    assert status == 404

    # Paying the rest clears the invoice, and a new import keeps the payments.
    post_form(server, "/payment", {
        "action": "add", "number": "1003", "amount": "400", "paid_on": "2026-11-18", "back": back})
    upload(server, FIXTURE.read_bytes())
    client = get(server + back)
    assert "Invoice 1003" not in client.split("</textarea>")[0]
    assert "Total due: $150.00" in client

    import re
    first_id = re.search(r"name=id value='(\d+)'", client).group(1)
    post_form(server, "/payment", {"action": "delete", "id": first_id, "back": back})
    assert "Invoice 1003 dated 2022-05-10: $200.00" in get(server + back)  # only the $400 is left
