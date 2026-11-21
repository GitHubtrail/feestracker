"""Client lists for a year (CSV, Excel or pasted names) and matching names across them.

Names are written many ways ("Shah, Pinky", "Pinky Shah", "Acme Bakery LLC",
"Acme Bakery, L.L.C."), so names are compared word by word after cleaning,
ignoring word order, punctuation, "and"/"&" and company endings like LLC or Inc.
Small spelling differences between words still count as the same word.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
from functools import lru_cache
from xml.etree import ElementTree

# Scores at or above SAME count as the same client. Between MAYBE and SAME the
# name is shown as a possible match for a person to check.
SAME = 0.9
MAYBE = 0.72

IGNORED_WORDS = {
    "and", "the", "of", "llc", "inc", "incorporated", "corp", "corporation", "co",
    "company", "ltd", "limited", "pllc", "pc", "pa", "lp", "llp", "plc", "dba",
}

NAME_HEADERS = (
    "client account name", "account name", "client name", "client", "full name",
    "customer name", "customer", "name", "account",
)


class ListError(ValueError):
    """The file could not be read as a list of client names."""


# ---------------------------------------------------------------- reading files


def read_names(data: bytes, filename: str = "") -> list[str]:
    """Client names from an uploaded CSV, Excel (.xlsx) or plain text file."""
    if data[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
        rows = _xlsx_rows(data)
    elif filename.lower().endswith(".xls") or data[:4] == b"\xd0\xcf\x11\xe0":
        raise ListError(
            "That is an old-style Excel file (.xls). In Excel, choose File → Save As and "
            "pick “Excel Workbook (.xlsx)” or “CSV”, then upload that."
        )
    else:
        rows = _text_rows(data.decode("utf-8-sig", errors="replace"))
    return names_from_rows(rows)


def looks_like_client_list(data: bytes, filename: str = "") -> bool:
    """Whether a file is a client list (for example a tax software return list)
    rather than a TaxDome bills export: it has name columns and no invoice column."""
    try:
        if data[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
            rows = _xlsx_rows(data)
        else:
            rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))
    except (ListError, csv.Error, ValueError):
        return False
    header = next(([c.strip().lower() for c in r] for r in rows if any(c.strip() for c in r)), [])
    if any("invoice" in h for h in header):
        return False
    return ("return name" in header or any(h in NAME_HEADERS for h in header)
            or ("first name" in header and "last name" in header))


def names_from_text(text: str) -> list[str]:
    """Names pasted into a box, one per line (a pasted spreadsheet column works too)."""
    return names_from_rows(_text_rows(text))


def _text_rows(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:20])
    if "\t" in sample:
        delim = "\t"
    elif any(_looks_like_header(c) for c in next(csv.reader([lines[0]]))) and "," in lines[0]:
        delim = ","
    elif all(ln.count(",") == lines[0].count(",") for ln in lines[:20]) and lines[0].count(",") > 1:
        delim = ","  # a real CSV with several columns
    else:
        # One name per line. "Shah, Pinky" is a single name, so don't split on commas.
        return [[ln.strip().strip('"')] for ln in lines]
    return [row for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]


def _looks_like_header(cell: str) -> bool:
    c = cell.strip().lower()
    return c in NAME_HEADERS or c in ("first name", "last name", "first", "last", "email", "phone")


def names_from_rows(rows: list[list[str]]) -> list[str]:
    rows = [[(c or "").strip() for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise ListError("No names found in that file.")
    header = [c.lower() for c in rows[0]]
    if "return name" in header and ("first name" in header or "company name" in header):
        names = _names_from_returns(header, rows[1:])
    else:
        names = _names_from_columns(header, rows)
    if not names:
        raise ListError("No names found in that file.")
    return names


def _names_from_columns(header: list[str], rows: list[list[str]]) -> list[str]:
    columns: list[int] = []
    for wanted in NAME_HEADERS:
        if wanted in header:
            columns = [header.index(wanted)]
            break
    else:
        first = next((i for i, h in enumerate(header) if h in ("first name", "first")), None)
        last = next((i for i, h in enumerate(header) if h in ("last name", "last", "surname")), None)
        if first is not None and last is not None:
            columns = [first, last]
        else:
            named = [i for i, h in enumerate(header) if "name" in h or "client" in h]
            columns = named[:1]
    if columns:
        rows = rows[1:]
    else:
        columns = [0]  # no header row: names are in the first column
    return _unique(" ".join(r[i] for i in columns if i < len(r) and r[i]) for r in rows)


# Rows in a tax software return list that are not clients.
NOT_A_CLIENT = re.compile(r"\b(sample|template|auto-saved return|test return)\b", re.I)


def _names_from_returns(header: list[str], rows: list[list[str]]) -> list[str]:
    """A tax software return list: one row per return, so a client can appear several
    times (estimates, extensions, amended returns). The client is the company name for
    business returns, or the filer and spouse for personal returns."""
    col = {h: i for i, h in reversed(list(enumerate(header)))}

    def get(r: list[str], h: str) -> str:
        i = col.get(h)
        return r[i].strip() if i is not None and i < len(r) else ""

    names = []
    for r in rows:
        if NOT_A_CLIENT.search(get(r, "return name")):
            continue
        company = _tidy(get(r, "company name"))
        filer = _tidy(" ".join(x for x in (get(r, "first name"), get(r, "last name")) if x))
        spouse = _tidy(" ".join(x for x in (get(r, "spouse first name"), get(r, "spouse last name")) if x))
        if company:
            name = company
        elif filer:
            name = f"{filer} and {spouse}" if spouse else filer
        else:
            name = _tidy(RETURN_SUFFIX.split(get(r, "return name"))[0])
        if len(re.findall(r"[A-Za-z]", name)) >= 2:
            names.append(name)
    return _unique(names)


# The end of a return name that describes the return, not the client: "-ext", "-ES q2",
# "-1040NR -amended 1", "W7", "-HOH", "- Minor".
RETURN_SUFFIX = re.compile(
    r"\s*[-,]?\s*\b(?:ext|es|q\d|1040\w*|amended|w-?7|itin|hoh|mfj|mfs|single|minor|paper|v\d|"
    r"\d{4}\s*es)\b.*$", re.I)


def _tidy(name: str) -> str:
    name = " ".join(name.split())
    return name.title() if name.isupper() and len(name) > 4 else name


def _unique(names) -> list[str]:
    out, seen = [], set()
    for name in names:
        name = name.strip()
        key = words(name) if name else ()
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _xlsx_rows(data: bytes) -> list[list[str]]:
    """The first sheet of an .xlsx file as rows of text, using only the standard library."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        sheet = _first_sheet(z) or (sheets[0] if sheets else None)
        if not sheet:
            raise ListError("That Excel file has no sheets.")
        root = ElementTree.fromstring(z.read(sheet))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ListError("That file could not be read as an Excel workbook.") from exc
    rows = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", ns):
            col = _column_index(c.get("r", "")) if c.get("r") else len(cells)
            kind = c.get("t", "")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            else:
                v = c.find("m:v", ns)
                text = v.text or "" if v is not None else ""
                if kind == "s" and text.isdigit() and int(text) < len(shared):
                    text = shared[int(text)]
            cells[col] = text
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def _first_sheet(z: zipfile.ZipFile) -> str | None:
    """The worksheet shown first in Excel, which is not always sheet1.xml."""
    try:
        wb = ElementTree.fromstring(z.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        return None
    first = next(iter(wb.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")), None)
    if first is None:
        return None
    rid = first.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target", "").lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    return None


def _column_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


# ---------------------------------------------------------------- matching


def words(name: str) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ").replace("l.l.c", "llc")
    text = re.sub(r"\(.*?\)", " ", text)  # nicknames like "(Siva)"
    tokens = re.findall(r"[a-z0-9]+", text.replace(".", ""))
    # Drop company endings, "and", and initials such as the "K" in "Konduru, Sasi K."
    kept = [t for t in tokens if t not in IGNORED_WORDS and (len(t) > 1 or t.isdigit())]
    return tuple(sorted(set(kept or tokens)))


def is_company(name: str) -> bool:
    lowered = " " + re.sub(r"[^a-z0-9]+", " ", name.lower().replace("l.l.c", "llc")) + " "
    return any(f" {w} " in lowered for w in IGNORED_WORDS - {"and", "the", "of"})


def is_couple(name: str) -> bool:
    """A name listing two people, like "Rivera Maria and Jose" (not a company)."""
    lowered = " " + re.sub(r"[^a-z0-9&]+", " ", name.lower()) + " "
    return (" and " in lowered or "&" in lowered) and not is_company(name)


@lru_cache(maxsize=200_000)
def _word_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def score(a: tuple[str, ...], b: tuple[str, ...], spouse_added: bool = False) -> float:
    """How alike two cleaned names are, from 0 to 1.

    Words may be in any order and spelled slightly differently. When one name
    has extra words, it is only the same client if those extra words are a
    spouse ("Vangoor Janakiramreddy" and "Vangoor Janakiramreddy and Swapna");
    otherwise ("DD Housing LLC" and "DD Housing Northwest LLC") it is only a
    possible match for a person to check.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    best = [max(_word_score(w, x) for x in long_) for w in short]
    if min(best) < 0.8:
        whole = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
        return whole * 0.85  # never "the same" on the spelling of the whole string alone
    s = sum(best) / len(best)
    if len(short) < len(long_):
        extra = [x for x in long_ if max(_word_score(x, w) for w in short) < 0.8]
        if len(short) >= 2 and min(best) >= 0.95 and all(len(x) <= 2 for x in extra):
            return s - 0.02  # only initials added, like "VS" in "S Sai Sandeep VS Ananthabhotla"
        if len(short) >= 2 and spouse_added and min(best) >= 0.95:
            return s - 0.03
        return min(s, 0.85) if len(short) >= 2 else min(s, 0.78)
    return s


@dataclass
class Match:
    name: str          # the name looked up
    found: str | None  # best name in the other list, if any
    score: float

    @property
    def same(self) -> bool:
        return self.found is not None and self.score >= SAME

    @property
    def maybe(self) -> bool:
        return self.found is not None and MAYBE <= self.score < SAME


class Matcher:
    """Finds the closest name in a fixed list of names."""

    def __init__(self, names: list[str]):
        self.names = list(names)
        self.cleaned = [words(n) for n in self.names]
        self.couples = [is_couple(n) for n in self.names]
        self.companies = [is_company(n) for n in self.names]
        self.exact: dict[tuple[str, ...], int] = {}
        self.by_word: dict[str, set[int]] = {}
        self.joined = {}
        for i, w in enumerate(self.cleaned):
            self.exact.setdefault(w, i)
            self.joined.setdefault(" ".join(w), i)
            for t in w:
                self.by_word.setdefault(t, set()).add(i)
        self.all_words = list(self.by_word)

    def best(self, name: str) -> Match:
        w = words(name)
        if w in self.exact:
            return Match(name, self.names[self.exact[w]], 1.0)
        candidates: set[int] = set()
        for t in w:
            candidates |= self.by_word.get(t, set())
            if len(t) > 3:
                for near in get_close_matches(t, self.all_words, n=5, cutoff=0.8):
                    candidates |= self.by_word[near]
        for near in get_close_matches(" ".join(w), list(self.joined), n=3, cutoff=0.7):
            candidates.add(self.joined[near])
        couple, company = is_couple(name), is_company(name)
        best_i, best_s = None, 0.0
        for i in candidates:
            c = self.cleaned[i]
            if len(w) > len(c):
                spouse_added = couple and not self.companies[i]
            else:
                spouse_added = self.couples[i] and not company
            s = score(w, c, spouse_added)
            if s > best_s or (s == best_s and best_i is not None and self.names[i] < self.names[best_i]):
                best_i, best_s = i, s
        if best_i is None or best_s < MAYBE:
            return Match(name, self.names[best_i] if best_i is not None else None, best_s)
        return Match(name, self.names[best_i], best_s)
