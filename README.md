# Fees Tracker

See who owes you, who has paid, and how much is still outstanding for each year,
using the invoice export from TaxDome.

Everything runs on your own computer. Your client data is saved in the `data/`
folder next to the app and is never uploaded anywhere or added to this repository.

## What you get

- **Summary**: how much is owed right now, collected and billed, a year-by-year table
  (billed, collected, still owed), how old the unpaid money is, and the biggest balances.
- **Who owes**: every client with money outstanding, biggest first, split into
  up to 30 days, 31 to 90 days, 91 days to 1 year, and over 1 year. Filter by year,
  or download the list as a spreadsheet (CSV).
- **Who paid**: clients ranked by what they paid, for all time or for one year.
- **Each client**: all their invoices and services, plus a ready-to-send payment
  reminder listing their open invoices and the total due, with a Copy button.
- **Import**: load a new TaxDome export whenever you like. Existing invoices are
  updated and new ones are added, so nothing is counted twice.
- **Data checks**: invoices where the TaxDome numbers don't add up, for example an
  invoice marked paid whose payments differ from its line items.

## Start it

You need Python 3.10 or newer (free from [python.org](https://www.python.org/downloads/)).
Nothing else needs installing.

- **Mac**: double-click `Start Fees Tracker.command`.
  The first time, macOS may ask you to confirm: right-click the file, choose **Open**, then **Open** again.
- **Windows**: double-click `Start Fees Tracker.bat`.
- **Any computer**: open a terminal in this folder and run `python3 -m feestracker`.

Your browser opens at <http://127.0.0.1:8765/>. Keep the small black window open while
you use the app, and close it when you're done.

## Load your TaxDome invoices

1. In TaxDome, go to **Billing → Invoices**, choose **Export**, and save the CSV file.
2. In Fees Tracker, click **Import**, choose that file, and click **Import**.

Do this again whenever you want fresh numbers.

## How the numbers are worked out

- **Billed** is the invoice's services minus its discounts.
- **Collected** counts card and bank payments that went through in TaxDome (failed or
  pending ones don't count), plus payments recorded on the invoice as a line such as
  "Payment Received on 5/9/2026", which is how Zelle, cash and cheque payments are entered.
- **Still owed** is billed minus collected, for invoices that are not marked paid.
  An invoice marked paid in TaxDome always counts as owing nothing.
- **Age** is counted from the invoice date.
- The **year** on the summary and on Who owes is the invoice's year. On Who paid it is the
  year the invoice was paid.

## For developers

```sh
python3 -m feestracker import path/to/bills.csv   # load an export from the command line
python3 -m feestracker summary                    # print totals
python3 -m pip install pytest && python3 -m pytest
```

Code lives in `feestracker/`: `taxdome.py` reads the export, `db.py` stores it in SQLite,
`reports.py` answers the questions, and `web.py` serves the pages using only the Python
standard library. Tests use made-up invoices in `tests/fixtures/sample_bills.csv`.
