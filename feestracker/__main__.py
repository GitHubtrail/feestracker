"""Start the app:  python -m feestracker

Other commands:
  python -m feestracker import path/to/bills.csv   load a TaxDome export
  python -m feestracker summary                    print totals in the terminal
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

from . import db, reports
from .taxdome import ImportError_, format_cents, read_invoices

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "feestracker.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feestracker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="where to keep the data")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="open the app in your browser (the default)")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--no-browser", action="store_true")
    p_import = sub.add_parser("import", help="load a TaxDome bills export")
    p_import.add_argument("csv", type=Path)
    sub.add_parser("summary", help="print totals")
    args = parser.parse_args(argv)
    args.db.parent.mkdir(parents=True, exist_ok=True)

    if args.command == "import":
        conn = db.connect(args.db)
        try:
            counts = db.save_invoices(conn, read_invoices(args.csv), args.csv.name)
        except (ImportError_, OSError) as exc:
            print(f"Could not import: {exc}", file=sys.stderr)
            return 1
        print(f"Imported {counts['invoices']} invoices "
              f"({counts['added']} new, {counts['updated']} updated).")
        return 0

    if args.command == "summary":
        conn = db.connect(args.db)
        s = reports.summary(conn)
        print(f"Invoices:  {s.invoices}")
        print(f"Billed:    {format_cents(s.billed_cents)}")
        print(f"Collected: {format_cents(s.collected_cents)}")
        print(f"Owed now:  {format_cents(s.outstanding_cents)}  "
              f"({s.open_invoices} invoices, {s.clients_owing} clients)")
        print("\nYear   Billed          Collected       Still owed")
        for y in reports.by_year(conn):
            print(f"{y['year']:<6} {format_cents(y['billed']):>15} "
                  f"{format_cents(y['collected']):>15} {format_cents(y['outstanding']):>15}")
        ages = reports.age_totals(reports.who_owes(conn, date.today()))
        print("\nOwed, by age:")
        for key, label, *_ in reports.AGE_BUCKETS:
            print(f"  {label:<20} {format_cents(ages[key]):>15}")
        return 0

    from .web import serve

    port = getattr(args, "port", 8765)
    server = serve(args.db, port=port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Fees Tracker is running at {url}")
    print("Keep this window open while you use it. Press Ctrl+C to stop.")
    if not getattr(args, "no_browser", False):
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
