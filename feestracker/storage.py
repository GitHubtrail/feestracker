"""Where your data lives, finding data left in older copies of the app, and updating the app.

Your data is kept in one fixed place, outside the app's own folder, so a new
version of the app finds it straight away:

    Documents/Fees Tracker/feestracker.db   (or "Fees Tracker" in your home folder)

Older versions kept it in a "data" folder inside the app folder, which was lost
whenever a new version was downloaded into a new folder.
"""

from __future__ import annotations

import io
import os
import shutil
import sqlite3
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DB_NAME = "feestracker.db"
UPDATE_URL = "https://codeload.github.com/GitHubtrail/feestracker/zip/refs/heads/main"

# What an update replaces in the app folder. Nothing else (and never your data) is touched.
APP_FILES = ("feestracker", "README.md", "Start Fees Tracker.bat", "Start Fees Tracker.command")


def data_dir() -> Path:
    """The folder that holds your data. FEESTRACKER_DATA can point somewhere else."""
    if os.environ.get("FEESTRACKER_DATA"):
        return Path(os.environ["FEESTRACKER_DATA"]).expanduser()
    home = Path.home()
    docs = home / "Documents"
    return (docs if docs.is_dir() else home) / "Fees Tracker"


def default_db() -> Path:
    return data_dir() / DB_NAME


# ---------------------------------------------------------------- older copies


@dataclass
class OldData:
    path: Path
    invoices: int
    bad_debts: int
    payments: int
    modified: datetime

    @property
    def adjustments(self) -> int:
        return self.bad_debts + self.payments


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.DatabaseError:
        return 0  # an older version without this table


def inspect(path: Path) -> OldData | None:
    """What a data file holds, without changing it. None if it isn't Fees Tracker data."""
    try:
        conn = _read_only(path)
        try:
            invoices = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
            found = OldData(path, invoices, _count(conn, "bad_debts"), _count(conn, "manual_payments"),
                            datetime.fromtimestamp(path.stat().st_mtime))
        finally:
            conn.close()
    except (sqlite3.DatabaseError, OSError):
        return None
    return found


def _candidate_paths(current: Path) -> list[Path]:
    """Data files in this and other copies of the app: in their "data" folder, in the
    folders where downloads usually get unzipped, up to two folders deep."""
    home = Path.home()
    roots = {APP_DIR, APP_DIR.parent, APP_DIR.parent.parent}
    for name in ("Downloads", "Desktop", "Documents", "OneDrive/Desktop", "OneDrive/Documents"):
        roots.add(home / name)
    found: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("data/" + DB_NAME, "*/data/" + DB_NAME, "*/*/data/" + DB_NAME):
            try:
                found.update(p.resolve() for p in root.glob(pattern))
            except OSError:
                continue
    current = current.resolve() if current.exists() else current
    return sorted(p for p in found if p != current)


def find_old_data(current: Path) -> list[OldData]:
    """Data left in other copies of the app, the ones with the most of your own changes first."""
    found = [d for d in (inspect(p) for p in _candidate_paths(current)) if d and d.invoices]
    return sorted(found, key=lambda d: (-d.adjustments, -d.invoices, -d.modified.timestamp()))


def move_old_data(target: Path) -> OldData | None:
    """The first time the app runs with data kept in `target`, copy in the best data
    found in an older copy of the app. The old file is left where it was."""
    if target.exists():
        return None
    old = find_old_data(target)
    if not old:
        return None
    best = old[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    src = _read_only(best.path)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return best


def bring_back_adjustments(conn: sqlite3.Connection, old: Path) -> dict[str, int]:
    """Copy bad debt marks and payments you entered from an older data file into this one.

    Only invoices that exist here are touched, and anything already here is kept,
    so doing this twice changes nothing.
    """
    conn.execute("ATTACH DATABASE ? AS old", (str(old),))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM old.sqlite_master WHERE type='table'")}
        marks = payments = 0
        with conn:
            if "bad_debts" in tables:
                marks = conn.execute(
                    """INSERT OR IGNORE INTO bad_debts (invoice_number, marked_at, note)
                       SELECT b.invoice_number, b.marked_at, b.note FROM old.bad_debts b
                       WHERE b.invoice_number IN (SELECT number FROM invoices)"""
                ).rowcount
            if "manual_payments" in tables:
                payments = conn.execute(
                    """INSERT INTO manual_payments (invoice_number, paid_on, amount_cents, note, entered_at)
                       SELECT p.invoice_number, p.paid_on, p.amount_cents, p.note, p.entered_at
                       FROM old.manual_payments p
                       WHERE p.invoice_number IN (SELECT number FROM invoices)
                         AND NOT EXISTS (
                           SELECT 1 FROM manual_payments m
                           WHERE m.invoice_number = p.invoice_number AND m.paid_on = p.paid_on
                             AND m.amount_cents = p.amount_cents AND m.note = p.note)"""
                ).rowcount
            if "removed_clients" in tables:
                conn.execute("""INSERT OR IGNORE INTO removed_clients (name, removed_at)
                                SELECT name, removed_at FROM old.removed_clients""")
    finally:
        conn.execute("DETACH DATABASE old")
    return {"bad_debts": marks, "payments": payments}


# ---------------------------------------------------------------- updating the app


class UpdateError(RuntimeError):
    pass


def can_update(app_dir: Path = APP_DIR) -> bool:
    """A developer's git checkout is updated with git, not with the button."""
    return not (app_dir / ".git").exists()


def update_app(app_dir: Path = APP_DIR, url: str = UPDATE_URL, fetch=None) -> None:
    """Download the newest version and put its files in place of this version's.

    Your data is not in the app folder, so it is not touched.
    """
    if not can_update(app_dir):
        raise UpdateError("This copy is a git checkout. Use git pull instead.")
    try:
        data = fetch(url) if fetch else urllib.request.urlopen(url, timeout=60).read()
    except OSError as exc:
        raise UpdateError("Could not download the update. Check your internet connection.") from exc
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UpdateError("The download was not a valid app archive.") from exc
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for member in archive.infolist():
            target = (tmp_path / member.filename).resolve()
            if not str(target).startswith(str(tmp_path.resolve())):
                raise UpdateError("The download had unexpected file paths.")
        archive.extractall(tmp_path)
        tops = [p for p in tmp_path.iterdir() if p.is_dir()]
        new = tops[0] if len(tops) == 1 else tmp_path
        if not (new / "feestracker" / "__init__.py").is_file():
            raise UpdateError("The download did not contain Fees Tracker.")
        for name in APP_FILES:
            src = new / name
            if not src.exists():
                continue
            dst = app_dir / name
            if src.is_dir():
                staged = app_dir / (name + ".new")
                shutil.rmtree(staged, ignore_errors=True)
                shutil.copytree(src, staged, ignore=shutil.ignore_patterns("__pycache__"))
                old = app_dir / (name + ".old")
                shutil.rmtree(old, ignore_errors=True)
                try:
                    if dst.exists():
                        dst.rename(old)
                    staged.rename(dst)
                except OSError:
                    # Windows can refuse to rename a folder that is in use: copy over it instead.
                    if old.exists() and not dst.exists():
                        old.rename(dst)
                    shutil.copytree(staged, dst, dirs_exist_ok=True)
                shutil.rmtree(old, ignore_errors=True)
                shutil.rmtree(staged, ignore_errors=True)
            elif not dst.exists() or dst.read_bytes() != src.read_bytes():
                # Windows reads a running .bat file as it goes, so only replace changed files.
                shutil.copy2(src, dst)
        command = app_dir / "Start Fees Tracker.command"
        if command.exists():
            command.chmod(0o755)
