import io
import zipfile

import pytest

from feestracker import db, storage, web
from feestracker.taxdome import read_invoices

from .conftest import FIXTURE


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A pretend home folder, with the app unzipped in Downloads."""
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    app = home / "Downloads" / "feestracker-main"
    (app / "feestracker").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("FEESTRACKER_DATA", raising=False)
    monkeypatch.setattr(storage, "APP_DIR", app)
    return home


def make_db(path, bad=(), payments=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    db.save_invoices(conn, read_invoices(FIXTURE), "test")
    for number in bad:
        db.mark_bad_debt(conn, number)
    for number, cents in payments:
        db.add_payment(conn, number, cents, "2026-11-15")
    conn.close()


def test_data_lives_in_documents(home):
    assert storage.default_db() == home / "Documents" / "Fees Tracker" / "feestracker.db"


def test_first_run_moves_the_data_with_your_changes(home):
    # An old copy where bad debts were marked, and a newer copy where the export was
    # imported again and the marks were lost.
    make_db(home / "Downloads" / "feestracker-main (1)" / "data" / "feestracker.db",
            bad=["1003"], payments=[("1004", 5000)])
    make_db(home / "Downloads" / "feestracker-main" / "data" / "feestracker.db")
    target = storage.default_db()
    moved = storage.move_old_data(target)
    assert moved and "feestracker-main (1)" in str(moved.path)
    found = storage.inspect(target)
    assert (found.invoices, found.bad_debts, found.payments) == (6, 1, 1)
    assert storage.move_old_data(target) is None  # only the first time


def test_bring_back_adjustments(home):
    old = home / "Desktop" / "feestracker-main" / "data" / "feestracker.db"
    make_db(old, bad=["1003"], payments=[("1004", 5000)])
    target = storage.default_db()
    make_db(target)
    assert [o.path for o in storage.find_old_data(target)] == [old.resolve()]
    conn = db.connect(target)
    assert storage.bring_back_adjustments(conn, old) == {"bad_debts": 1, "payments": 1}
    assert storage.bring_back_adjustments(conn, old) == {"bad_debts": 0, "payments": 0}
    assert conn.execute("SELECT COUNT(*) FROM manual_payments").fetchone()[0] == 1
    conn.close()


def zip_of(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr("feestracker-main/" + name, text)
    return buf.getvalue()


def test_update_app(tmp_path):
    app = tmp_path / "app"
    (app / "feestracker").mkdir(parents=True)
    (app / "feestracker" / "__init__.py").write_text("old")
    (app / "feestracker" / "gone.py").write_text("old")
    (app / "data").mkdir()
    (app / "data" / "feestracker.db").write_text("keep me")
    new = zip_of({"feestracker/__init__.py": "new", "README.md": "new readme",
                  "Start Fees Tracker.command": "#!/bin/bash"})
    storage.update_app(app, fetch=lambda url: new)
    assert (app / "feestracker" / "__init__.py").read_text() == "new"
    assert not (app / "feestracker" / "gone.py").exists()
    assert (app / "README.md").read_text() == "new readme"
    assert (app / "data" / "feestracker.db").read_text() == "keep me"


def test_update_refuses_bad_downloads(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    with pytest.raises(storage.UpdateError):
        storage.update_app(app, fetch=lambda url: b"not a zip")
    with pytest.raises(storage.UpdateError):
        storage.update_app(app, fetch=lambda url: zip_of({"README.md": "x"}))
    (app / ".git").mkdir()
    with pytest.raises(storage.UpdateError):
        storage.update_app(app, fetch=lambda url: zip_of({"feestracker/__init__.py": "x"}))


def test_data_page_brings_back_changes(home):
    import threading
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer

    old = home / "Downloads" / "old" / "data" / "feestracker.db"
    make_db(old, bad=["1003"])
    target = storage.default_db()
    make_db(target)
    calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(target, update_fn=lambda: calls.append(1)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        html = urllib.request.urlopen(base + "/data").read().decode()
        assert str(target.parent) in html and "Bring back bad debts and payments" in html

        def post(path, fields):
            req = urllib.request.Request(base + path, data=urllib.parse.urlencode(fields).encode())
            return urllib.request.urlopen(req).read().decode()

        assert "That data file was not found" in post("/data/bring-back", {"path": "/etc/passwd"})
        assert "Brought back 1 bad debt and 0 payments" in post("/data/bring-back", {"path": str(old.resolve())})
        assert "Updated." in post("/update", {}) and calls == [1]
    finally:
        srv.shutdown()
        srv.server_close()


def test_update_explains_a_private_repo(tmp_path):
    import urllib.error
    import pytest
    from feestracker import storage

    def fetch(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    with pytest.raises(storage.UpdateError, match="private"):
        storage.update_app(tmp_path, fetch=fetch)
