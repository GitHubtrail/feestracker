"""Client lists for a year (CSV, Excel or pasted names) and matching names across them.

Names are written many ways ("Lee, Dana", "Dana Lee", "Acme Bakery LLC",
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
    # Professional titles after a name, like "Sam Lee DDS PC".
    "dds", "dmd", "md", "cpa", "dvm", "esq",
}

PROFESSIONAL = {"dds", "dmd", "md", "cpa", "dvm", "esq"}

# Words many business names share, so matching on them alone means nothing
# ("Oakfield Real Estate Group" is not "R & R Realty Group").
GENERIC_WORDS = {
    "group", "real", "realty", "estate", "properties", "property", "holdings", "investments",
    "services", "service", "solutions", "consulting", "management", "enterprises", "associates",
    "dental", "medical", "clinic", "health", "care", "family", "shop", "store", "international",
    "global", "usa", "america", "american", "trading", "partners", "capital", "ventures",
}

# Name endings added to a first name, like "Rajbhai" or "Nileshkumar".
HONORIFIC = re.compile(r"(?<=[a-z]{3})(bhai|ben|kumar)$")

NAME_HEADERS = (
    "client account name", "account name", "client name", "client", "full name",
    "customer name", "customer", "name", "account",
)


class ListError(ValueError):
    """The file could not be read as a list of client names."""


# ---------------------------------------------------------------- reading files


def read_names(data: bytes, filename: str = "") -> list[str]:
    """Client names from an uploaded CSV, Excel (.xlsx) or plain text file."""
    return names_from_rows(_file_rows(data, filename))


def read_aliases(data: bytes, filename: str = "") -> dict[str, str]:
    """For a tax software return list, each client's return name when it differs from
    the name kept ("Moreno Raul & Diaz Elena" for "Raul Castillo
    and Elena Diaz"), since TaxDome is often spelled like the return name."""
    try:
        rows = [[(c or "").strip() for c in r] for r in _file_rows(data, filename) if any((c or "").strip() for c in r)]
    except ListError:
        return {}
    if not rows:
        return {}
    header = [c.lower() for c in rows[0]]
    if "return name" not in header or not ("first name" in header or "company name" in header):
        return {}
    return {name: alias for name, alias in _returns(header, rows[1:]) if alias and alias != name}


def _file_rows(data: bytes, filename: str) -> list[list[str]]:
    if data[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
        rows = _xlsx_rows(data)
    elif filename.lower().endswith(".xls") or data[:4] == b"\xd0\xcf\x11\xe0":
        raise ListError(
            "That is an old-style Excel file (.xls). In Excel, choose File → Save As and "
            "pick “Excel Workbook (.xlsx)” or “CSV”, then upload that."
        )
    else:
        rows = _text_rows(data.decode("utf-8-sig", errors="replace"))
    return rows


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
        # One name per line. "Lee, Dana" is a single name, so don't split on commas.
        return [[ln.strip().strip('"')] for ln in lines]
    return [row for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]


def _looks_like_header(cell: str) -> bool:
    c = cell.strip().lower()
    return c in NAME_HEADERS or c in ("first name", "last name", "first", "last", "email", "phone", "return name")


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
    return _unique(name for name, _ in _returns(header, rows))


def _returns(header: list[str], rows: list[list[str]]) -> list[tuple[str, str]]:
    """(client name, return name) for each return that is a client's."""
    col = {h: i for i, h in reversed(list(enumerate(header)))}

    def get(r: list[str], h: str) -> str:
        i = col.get(h)
        return r[i].strip() if i is not None and i < len(r) else ""

    out = []
    for r in rows:
        if NOT_A_CLIENT.search(get(r, "return name")):
            continue
        company = _tidy(get(r, "company name"))
        filer = _tidy(" ".join(x for x in (get(r, "first name"), get(r, "last name")) if x))
        spouse = _tidy(" ".join(x for x in (get(r, "spouse first name"), get(r, "spouse last name")) if x))
        return_name = _tidy(RETURN_SUFFIX.split(get(r, "return name"))[0])
        if company:
            name = company
        elif filer:
            name = f"{filer} and {spouse}" if spouse else filer
        else:
            name = return_name
        if len(re.findall(r"[A-Za-z]", name)) >= 2:
            out.append((name, return_name))
    return out


# The end of a return name that describes the return, not the client: "-ext", "-ES q2",
# "-1040NR -amended 1", "W7", "-HOH", "- Minor".
RETURN_SUFFIX = re.compile(
    r"\s*[-,(]?\s*\b(?:ext|es|q\d|1040\w*|amended|w-?7|itin|hoh|mfj|mfs|single|minor|paper|v\d|"
    r"\d{4}\s*es|filing\s+copy|final\s+return|filed|to\s+file|use\s+this)\b.*$"
    # A business form number after a dash: "Acme LLC-1120S", "Two Oaks - 1065".
    r"|\s*-\s*(?:1120\w*|1065|1041|990\w*|5500\w*)\b.*$", re.I)


def looks_like_efile_list(data: bytes, filename: str = "") -> bool:
    """Whether a file is the tax software's e-file list (one row per e-filed return,
    federal and state, with its status), not a return list."""
    try:
        rows = _file_rows(data, filename)
    except ListError:
        return False
    header = next(([c.strip().lower() for c in r] for r in rows if any(c.strip() for c in r)), [])
    return {"return name", "jurisdiction", "status"} <= set(header) and "e-file id" in header


def read_efiled(data: bytes, filename: str = "") -> list[str]:
    """Return names of the accepted federal returns (personal and business) in an e-file
    list. State returns, extensions and rejected or unsent returns are left out."""
    rows = [[(c or "").strip() for c in r] for r in _file_rows(data, filename) if any((c or "").strip() for c in r)]
    if not rows:
        return []
    col = {h.lower(): i for i, h in reversed(list(enumerate(rows[0])))}

    def get(r: list[str], h: str) -> str:
        i = col.get(h)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if (get(r, "jurisdiction").lower() != "federal" or not get(r, "status").lower().startswith("accepted")
                or get(r, "sub type").lower() == "extension" or get(r, "type") in ("4868", "7004")
                or NOT_A_CLIENT.search(get(r, "return name"))):
            continue
        name = RETURN_SUFFIX.split(get(r, "return name"))[0]
        name = re.sub(r"\((?:copy|filing)[^)]*\)", " ", name, flags=re.I)
        # A note after the name: "Lee Dana - DanaLee", "Ray Ann pspv-6794".
        trimmed = re.sub(r"(\s+-\s*\S+|\s+\S*\d\S*)$", "", name.strip())
        if len(trimmed.split()) >= 2:
            name = trimmed
        name = _tidy(name.strip(" -,("))
        if len(re.findall(r"[A-Za-z]", name)) >= 2:
            out.append(name)
    return _unique(out)


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
    # Drop company endings, "and", and initials such as the "K" in "Lopez, Ana K."
    kept = [HONORIFIC.sub("", t) for t in tokens if t not in IGNORED_WORDS and (len(t) > 1 or t.isdigit())]
    return tuple(sorted(set(kept or tokens)))


def squashed(name: str) -> str:
    """A name's letters with spaces and endings removed, so "Sun Rise Llc" and
    "SunRise LLC", or "Bay View Dental Associates Pc" and "BayView Dental Associates, PC",
    come out the same."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)", " ", text.replace("&", " and ").replace("l.l.c", "llc").replace(".", ""))
    return "".join(t for t in re.findall(r"[a-z0-9]+", text) if t not in IGNORED_WORDS)


def is_company(name: str) -> bool:
    lowered = " " + re.sub(r"[^a-z0-9]+", " ", name.lower().replace(".", "")) + " "
    return any(f" {w} " in lowered for w in IGNORED_WORDS - {"and", "the", "of"})


def is_professional(name: str) -> bool:
    """A practice named after a person, like "Sam Lee DDS PC" or "Ana Ruiz MD LLC"."""
    return bool(PROFESSIONAL & set(re.findall(r"[a-z]+", name.lower().replace(".", ""))))


def is_couple(name: str) -> bool:
    """A name listing two people, like "Rivera Maria and Jose" (not a company)."""
    lowered = " " + re.sub(r"[^a-z0-9&]+", " ", name.lower()) + " "
    return (" and " in lowered or "&" in lowered) and not is_company(name)


@lru_cache(maxsize=200_000)
def _word_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def ordered_words(name: str) -> tuple[str, ...]:
    """A person's name words in reading order, "First Middle Last". A name written
    "Last, First Middle" is turned round."""
    if "," in name:
        last, rest = name.split(",", 1)
        name = rest + " " + last
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)", " ", text.replace(".", ""))
    kept = [t for t in re.findall(r"[a-z0-9]+", text) if t not in IGNORED_WORDS and len(t) > 1]
    return tuple(dict.fromkeys(kept))


def middle_name_added(a: str, b: str) -> bool:
    """True when one person's name is the other's two words plus a middle name:
    "Pedro Ruiz" and "Ruiz, Pedro Antonio", "Pedro Antonio Ruiz" or
    "Ruiz Pedro Antonio". The extra word must sit where a middle name goes, so
    "Luis Soto" is not "Soto, Marco Luis"."""
    x, y = ordered_words(a), ordered_words(b)
    (short, s_raw), (long_, l_raw) = sorted([(x, a), (y, b)], key=lambda t: len(t[0]))
    if len(short) != 2 or len(long_) != 3:
        return False
    if (long_[0], long_[2]) == short:
        return True  # First Middle Last
    # "Last First Middle" written without a comma
    return "," not in l_raw and set(long_[:2]) == set(short) and "," not in s_raw


def score(a: tuple[str, ...], b: tuple[str, ...], spouse_added: bool = False) -> float:
    """How alike two cleaned names are, from 0 to 1.

    Words may be in any order and spelled slightly differently. When one name
    has extra words, it is only the same client if those extra words are a
    spouse ("Ortiz Carlos" and "Ortiz Carlos and Elena");
    otherwise ("AB Housing LLC" and "AB Housing Northwest LLC") it is only a
    possible match for a person to check.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    best = [max(_word_score(w, x) for x in long_) for w in short]
    if all(w in GENERIC_WORDS for w in short):
        return 0.0  # only words like "Group" or "Realty" in common
    if min(best) < 0.8:
        whole = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
        return whole * 0.85  # never "the same" on the spelling of the whole string alone
    s = sum(best) / len(best)
    if len(short) < len(long_):
        extra = [x for x in long_ if max(_word_score(x, w) for w in short) < 0.8]
        if len(short) >= 2 and min(best) >= 0.95 and all(len(x) <= 2 for x in extra):
            return s - 0.02  # only initials added, like "KS" in "J Paul KS Moreno"
        if len(short) >= 2 and spouse_added and min(best) >= 0.95:
            return s - 0.03
        if len(short) >= 2:
            return min(s, 0.85)
        # One word against a longer name ("Dario" for "Mario Rossi") is only worth
        # offering when that word is spelled exactly the same.
        return 0.78 if s == 1.0 else 0.6
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
        self.professional = [is_professional(n) for n in self.names]
        self.exact: dict[tuple[str, ...], int] = {}
        self.by_word: dict[str, set[int]] = {}
        self.joined = {}
        for i, w in enumerate(self.cleaned):
            self.exact.setdefault(w, i)
            self.joined.setdefault(" ".join(w), i)
            for t in w:
                self.by_word.setdefault(t, set()).add(i)
        self.all_words = list(self.by_word)
        self.squashed: dict[str, int] = {}
        for i, n in enumerate(self.names):
            self.squashed.setdefault(squashed(n), i)

    def different_kind(self, name: str, i: int, exact: bool = False) -> bool:
        """A person or couple against a company: "Sam Lee and Ana Lee" is not
        "Sam Lee DDS PC", and "Raul Moreno Corporation" is not the couple's return."""
        company, couple, pro = is_company(name), is_couple(name), is_professional(name)
        if company == self.companies[i]:
            return False
        if (couple or self.couples[i]) and not exact:  # same words, like "Chen and Sons" / "Chen & Sons Inc", is fine
            return True
        return pro or self.professional[i]  # "Ana Ruiz" vs "Ana Ruiz MD LLC"

    def best(self, name: str, reject: frozenset = frozenset()) -> Match:
        """The closest name, leaving out any in reject (names said not to be this one)."""
        w = words(name)
        i = self.exact.get(w)
        if i is not None and self.names[i] not in reject and not self.different_kind(name, i, exact=True):
            return Match(name, self.names[i], 1.0)
        sq = squashed(name)
        i = self.squashed.get(sq)
        if len(sq) >= 6 and i is not None and self.names[i] not in reject and not self.different_kind(name, i, exact=True):
            return Match(name, self.names[i], 1.0)
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
            if self.names[i] in reject or self.different_kind(name, i):
                continue
            c = self.cleaned[i]
            if len(w) > len(c):
                spouse_added = couple and not self.companies[i]
            else:
                spouse_added = self.couples[i] and not company
            s = score(w, c, spouse_added)
            if s < SAME and not company and not self.companies[i] and middle_name_added(name, self.names[i]):
                s = 0.91
            if s > best_s or (s == best_s and best_i is not None and self.names[i] < self.names[best_i]):
                best_i, best_s = i, s
        if best_s < SAME and couple and " and " in name.lower():
            # A couple whose return TaxDome has under the filer alone, with a middle name:
            # "Pedro Ruiz and Carmen Ruiz" for "Ruiz, Pedro Antonio".
            filer = re.split(r"\s+and\s+", name, maxsplit=1, flags=re.I)[0]
            m = self.best(filer, reject)
            if m.same and len(words(filer)) >= 2 and not self.couples[self.names.index(m.found)]:
                return Match(name, m.found, m.score - 0.005)
        if best_i is None or best_s < MAYBE:
            return Match(name, self.names[best_i] if best_i is not None else None, best_s)
        return Match(name, self.names[best_i], best_s)
