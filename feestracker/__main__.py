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

from . import __version__, db, reports, storage
from .taxdome import ImportError_, format_cents, read_invoices



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feestracker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=None,
                        help="where to keep the data (default: Documents/Fees Tracker)")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="open the app in your browser (the default)")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--no-browser", action="store_true")
    p_import = sub.add_parser("import", help="load a TaxDome bills export")
    p_import.add_argument("csv", type=Path)
    sub.add_parser("summary", help="print totals")
    args = parser.parse_args(argv)
    if args.db is None:
        args.db = storage.default_db()
        moved = storage.move_old_data(args.db)
        if moved:
            print(f"Moved your data from {moved.path.parent.parent} to {args.db.parent}.")
            print("From now on it is kept there, so new versions of the app find it.")
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
        extra = reports.overpayment_totals(conn)
        if any(t["invoices"] for t in extra.values()):
            print("Paid above invoice amounts (not in the totals):")
            for key, label, _ in reports.OVERPAYMENT_KINDS:
                t = extra[key]
                if t["invoices"]:
                    print(f"  {label}: {format_cents(t['cents'])} ({t['invoices']} invoices)")
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

    server = serve(args.db, port=getattr(args, "port", 8765))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Fees Tracker {__version__} is running at {url}")
    print(f"Your data is kept in {args.db.parent}")
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
