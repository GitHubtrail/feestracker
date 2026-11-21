# Fees Tracker

See who owes you, who has paid, and how much is still outstanding for each year,
using the invoice export from TaxDome.

Everything runs on your own computer. Your client data is saved in
**Documents/Fees Tracker** (or a "Fees Tracker" folder in your home folder) and is never
uploaded anywhere or added to this repository. Because it is kept outside the app's folder,
a new version of the app finds it straight away.

## What you get

- **Summary**: how much is owed right now, collected and billed, a year-by-year table
  (billed, collected, still owed), how old the unpaid money is, and the biggest balances.
- **Who owes**: every client with money outstanding, biggest first, split into
  up to 30 days, 31 to 90 days, 91 days to 1 year, and over 1 year. Filter by year,
  or download the list as a spreadsheet (CSV).
- **Unpaid by year**: every client who still owes on invoices from the years you tick
  (2023 and 2024 to start with), with one column per year. Download it as a CSV too.
- **Who paid**: clients ranked by what they paid, for all time or for one year.
- **Each client**: all their invoices and services, plus a ready-to-send payment
  reminder listing their open invoices and the total due, with a Copy button.
- **Part payments**: on a client's page, click **Record payment** under an open invoice, type
  the amount received (it can be less than the invoice), the date and an optional note such as
  "Zelle", and click **Save payment**. The rest stays owed everywhere in the app, and your
  payments are kept when you import a new export. If you later record the same payment in
  TaxDome too, delete it in Fees Tracker so it isn't counted twice.
- **Bad debts**: on a client's page, click **Mark as bad debt** next to an invoice that will
  never be paid. It is then left out of "owed" everywhere and shown on its own, in the
  summary, the year-by-year table and the Bad debts page. Click **Undo bad debt** to bring
  it back. Your marks are kept when you import a new export.
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

## Update the app

Click **Your data & updates**, then **Update app**. When it says it's done, close the black
window and open Fees Tracker again. Your data is not touched, so there is nothing to import
or copy. (Downloading a new ZIP still works too: the new copy finds your data in Documents.)

The first time this version runs, it looks for data left in older copies of the app (their
`data` folder, in Downloads, Desktop or Documents) and copies in the one with the most bad
debts and payments you entered. The old files are left where they were.

If bad debts or payments you entered went missing after an update, open **Your data &
updates**: it lists data found in older copies, and **Bring back bad debts and payments**
copies them into your current data without changing anything else.

## Load your TaxDome invoices

1. In TaxDome, go to **Billing → Invoices**, choose **Export**, and save the CSV file.
2. In Fees Tracker, click **Import**, choose that file, and click **Import**.

Do this again whenever you want fresh numbers.

## How the numbers are worked out

- **Billed** is the invoice's services minus its discounts.
- **Collected** counts card and bank payments that went through in TaxDome (failed or
  pending ones don't count), plus payments recorded on the invoice as a line such as
  "Payment Received on 5/9/2026", which is how Zelle, cash and cheque payments are entered.
- Payments you record in Fees Tracker are added to what TaxDome says was collected.
- **Still owed** is billed minus collected, for invoices that are not marked paid.
  An invoice marked paid in TaxDome always counts as owing nothing.
- **Bad debt** is what was still owed on invoices you marked as bad debt. If a client
  pays one later, only what is left unpaid counts as written off.
- On the Summary, **billed = collected + owed + bad debt**, overall and for each year.
  Collected there counts what was paid toward each invoice, up to its amount. Money paid
  above an invoice's amount (for example one payment that covered several invoices) is
  shown on its own line as "paid above invoice", and those invoices are listed on Data checks.
  Who paid and each client's page still show everything the client actually paid.
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
