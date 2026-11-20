from datetime import date
from pathlib import Path

import pytest

from feestracker import db
from feestracker.taxdome import read_invoices

FIXTURE = Path(__file__).parent / "fixtures" / "sample_bills.csv"
TODAY = date(2026, 11, 20)


@pytest.fixture
def invoices():
    return {inv.number: inv for inv in read_invoices(FIXTURE)}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.save_invoices(c, read_invoices(FIXTURE), FIXTURE.name)
    yield c
    c.close()
