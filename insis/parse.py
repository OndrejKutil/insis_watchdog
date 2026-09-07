"""
Turning InSIS HTML into the dataclasses in models.py.

InSIS is server-rendered Perl with plain <table> markup and no useful ids or
classes on rows, so everything here works the same way: find the table by its
header cells, derive column indexes from that header, then read rows by text.
Never by position - column order is not contractual and has no reason to stay
stable across semesters.
"""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import Course, Slot

BASE = "https://insis.vse.cz/"

# Header cell sets that identify the tables we care about.
RESULTS_HEAD = {"Kód", "Předmět", "Zápis"}
ARCH_HEAD = {"Kód", "Předmět", "Rozvrh"}
# Only Den+Čas are required. Kapacita is absent on some courses, and demanding
# it made whole timetables parse to nothing.
TIMETABLE_HEAD = {"Den", "Čas"}
COL_DAY, COL_TIME = "Den", "Čas"

_SLOT_COLUMNS = {
    "day": "Den", "time": "Čas", "kind": "Typ", "frequency": "Četnost",
    "room": "Místnost", "teacher": "Vyučující", "capacity": "Kapacita",
    "note": "Poznámka",
}


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _lines(node) -> list[str]:
    """
    Cell text split on <br>.

    InSIS puts several timetable entries in one cell separated by <br> -- a
    seminar and a lecture, typically. Flattening them to one string makes a
    column that cannot be read at any sane width, so keep them apart.
    """
    if node is None:
        return []
    out, cur = [], []
    for child in node.descendants:
        name = getattr(child, "name", None)
        if name == "br":
            out.append(" ".join("".join(cur).split()))
            cur = []
        elif name is None:
            cur.append(str(child))
    out.append(" ".join("".join(cur).split()))
    return [x for x in out if x]


def _headers(table) -> list[str]:
    rows = table.find_all("tr")
    if not rows:
        return []
    return [_text(c) for c in rows[0].find_all(["th", "td"])]


def _find_table(soup, needed: set[str]):
    """First table whose header row contains all of `needed`."""
    for table in soup.find_all("table"):
        if needed <= set(_headers(table)):
            return table
    return None


def _find_tables(soup, needed: set[str]) -> list:
    """
    Every table whose header row contains all of `needed`.

    Courses that split seminars and lectures render one table per type, so
    taking only the first would silently drop half the timetable.
    """
    return [tb for tb in soup.find_all("table")
            if needed <= set(_headers(tb))]


def _index_map(headers: list[str], wanted: dict[str, str]) -> dict[str, int]:
    """{'day': 'Den'} -> {'day': 3}. Missing columns are simply absent."""
    out = {}
    for key, label in wanted.items():
        if label in headers:
            out[key] = headers.index(label)
    return out


def _cell(cells, idx: int | None) -> str:
    if idx is None or idx >= len(cells):
        return ""
    return _text(cells[idx])


def parse_capacity(text: str) -> tuple[int | None, int | None]:
    """'16/16' -> (16, 16). Anything unexpected -> (None, None)."""
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)\s*$", text or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _ids_from_url(url: str) -> tuple[str, str]:
    """
    Pull predmet and zapis out of a vyber_cviceni link.

    InSIS separates query params with ';' rather than '&', which parse_qs does
    not split on, so normalise first.
    """
    q = urlsplit(url).query.replace(";", "&")
    params = parse_qs(q)
    return (params.get("predmet", [""])[0], params.get("zapis", [""])[0])


def parse_courses(html: str, base: str = BASE) -> list[Course]:
    """
    Read registrace.pl.

    `base` must be the URL the page was fetched from, not the site root: the
    timetable links are relative and resolving them against the root drops
    '/auth/' from the path.

    Two tables matter. The 'Arch' table lists every enrolled course and carries
    the link to its timetable chooser; the results table above it holds the
    Zápis column, i.e. whether a place was actually assigned. They are joined
    on the course code.
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- Zápis status, keyed by course code -------------------------------
    zapis_by_code: dict[str, str] = {}
    results = _find_table(soup, RESULTS_HEAD)
    if results:
        heads = _headers(results)
        ix = _index_map(heads, {"code": "Kód", "zapis": "Zápis"})
        for row in results.find_all("tr")[1:]:
            cells = row.find_all("td")
            code = _cell(cells, ix.get("code"))
            if code:
                zi = ix.get("zapis")
                zapis_by_code[code] = (
                    _lines(cells[zi]) if zi is not None and zi < len(cells)
                    else [])

    # --- the courses themselves -------------------------------------------
    arch = _find_table(soup, ARCH_HEAD)
    if not arch:
        return []

    heads = _headers(arch)
    ix = _index_map(heads, {
        "code": "Kód", "name": "Předmět", "group": "Skupina",
        "klass": "Třída", "schedule": "Rozvrh",
    })

    courses: list[Course] = []
    for row in arch.find_all("tr")[1:]:
        cells = row.find_all("td")
        code = _cell(cells, ix.get("code"))
        if not code:
            continue

        url = None
        sched_idx = ix.get("schedule")
        if sched_idx is not None and sched_idx < len(cells):
            link = cells[sched_idx].find(
                "a", href=lambda h: h and "vyber_cviceni" in h)
            if link:
                url = urljoin(base, link["href"])

        predmet, zapis_id = _ids_from_url(url) if url else ("", "")
        courses.append(Course(
            code=code,
            name=_cell(cells, ix.get("name")),
            group=_cell(cells, ix.get("group")),
            klass=_cell(cells, ix.get("klass")),
            schedule_url=url,
            schedule_text=_cell(cells, sched_idx),
            zapis_parts=zapis_by_code.get(code, []),
            predmet=predmet,
            zapis_id=zapis_id,
        ))
    return courses


def parse_slots(html: str) -> list[Slot]:
    """
    Read vyber_cviceni.pl - the 'Volba rozvrhové akce' timetable.

    A course with both a lecture and a seminar puts *both* in one table,
    separated by a repeated header row, and gives each its own radio group
    (`_akce_1` for Přednáška, `_akce_2` for Cvičení, confirmed 2026-09-07).
    So this parses the table as a sequence of sections:

      * a repeated header row starts a new section, and its columns are
        re-read -- do not assume both sections share a column order;
      * every row in a section belongs to that section's radio group, which
        is backfilled from whichever row in it actually carries a radio
        (full rows have none, so the first row alone is not enough);
      * without both, the repeated header parses as a bogus slot and a
        seminar can be written into the lecture's radio group.

    The radio is InSIS's own verdict on whether a slot may be taken; full rows
    simply have none. That is more reliable than comparing capacity numbers,
    so both are recorded and the radio wins.
    """
    soup = BeautifulSoup(html, "html.parser")
    slots: list[Slot] = []

    for table in _find_tables(soup, TIMETABLE_HEAD):
        heads = _headers(table)
        ix = _index_map(heads, _SLOT_COLUMNS)
        section: list[Slot] = []

        def flush(sec):
            """Give every row in the section the group name found in it."""
            if not sec:
                return
            name = next((s.group_name for s in sec if s.group_name), "")
            for s in sec:
                if not s.group_name:
                    s.group_name = name
            slots.extend(sec)

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            day = _cell(cells, ix.get("day"))
            time_ = _cell(cells, ix.get("time"))

            # A repeated header opens the next section.
            if day == COL_DAY and time_ == COL_TIME:
                flush(section)
                section = []
                new_heads = [_text(c) for c in cells]
                if TIMETABLE_HEAD <= set(new_heads):
                    ix = _index_map(new_heads, _SLOT_COLUMNS)
                continue

            if not day and not time_:      # spacer row
                continue

            joined, capacity = parse_capacity(_cell(cells, ix.get("capacity")))
            radio = cells[0].find("input", attrs={"type": "radio"})
            section.append(Slot(
                index=0,
                day=day,
                time=time_,
                kind=_cell(cells, ix.get("kind")),
                frequency=_cell(cells, ix.get("frequency")),
                room=_cell(cells, ix.get("room")),
                teacher=_cell(cells, ix.get("teacher")),
                joined=joined,
                capacity=capacity,
                note=_cell(cells, ix.get("note")),
                action_id=radio.get("value") if radio else None,
                group_name=radio.get("name", "") if radio else "",
                selected=bool(radio and radio.has_attr("checked")),
            ))
        flush(section)

    for i, s in enumerate(slots):
        s.index = i
    return slots


def parse_form(html: str, needle: str = "") -> tuple[str, str, dict[str, str]]:
    """
    Extract a form as (method, action, fields).

    Used for both logging in and submitting a timetable choice. Every hidden
    field is carried forward verbatim: InSIS threads a lot of state through
    them (destination, auth_id_hidden, zapis, akce...) and dropping any of it
    silently changes what the POST means.

    `needle` picks the form whose markup contains that string, for pages with
    more than one.
    """
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    if needle:
        forms = [f for f in forms if needle in str(f)] or forms
    if not forms:
        raise ValueError("no form on page")

    form = forms[0]
    fields: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("submit", "button", "image"):
            continue
        if itype in ("checkbox", "radio"):
            # Only pre-checked boxes are part of the submission.
            if inp.has_attr("checked"):
                fields[name] = inp.get("value", "on")
            continue
        fields[name] = inp.get("value", "")

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        if opt is not None:
            fields[name] = opt.get("value", "")

    return (
        (form.get("method") or "get").lower(),
        form.get("action") or "",
        fields,
    )


def find_links(html: str, base: str, needle: str) -> list[tuple[str, str]]:
    """
    Every (label, absolute-url) whose href contains `needle`, de-duplicated.

    Resolved against `base`, which must be the URL the page came from -- InSIS
    hrefs are relative and joining them onto the site root silently drops
    '/auth/'.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        if needle not in a["href"]:
            continue
        url = urljoin(base, a["href"])
        if url in seen:
            continue
        seen.add(url)
        out.append((_text(a) or url, url))
    return out


def radio_group_name(html: str, needle: str = "_ulozit_rozvrh") -> str | None:
    """
    Name of the radio group in the timetable form ('_akce_2' as of 2026-09).

    Discovered rather than hardcoded: the trailing number looks like an InSIS
    internal counter and there is no reason to trust it across semesters.
    """
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        if needle and needle not in str(form):
            continue
        radio = form.find("input", attrs={"type": "radio"})
        if radio and radio.get("name"):
            return radio["name"]
    return None


# Fields that carry login state and must never be mistaken for the code box.
# 'auth_2fa_type' is the trap: its name contains '2fa' but it is a hidden
# selector, and writing the code into it breaks the POST.
_NOT_CODE = {"auth_2fa_type", "auth_id_hidden", "login_hidden", "destination",
             "lang", "credential_0", "credential_1", "credential_cookie"}


def find_code_field(html: str) -> str | None:
    """
    Name of the input on the second login page that wants the 6-digit code.

    The 2FA page could not be captured (it needs real credentials), so rather
    than trusting a name pattern this restricts itself to inputs that are
    *visibly fillable and currently empty* -- a hidden field can never be the
    box the user types into. Among those, an obviously code-ish name wins,
    then InSIS's own 'credential_k', which sits unused on the first login form.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for form in soup.find_all("form"):
        for inp in form.find_all("input"):
            name = inp.get("name")
            itype = (inp.get("type") or "text").lower()
            if not name or name in _NOT_CODE:
                continue
            if itype not in ("text", "password", "tel", "number"):
                continue
            if inp.get("value"):
                continue
            candidates.append(name)

    if not candidates:
        return None
    for pattern in (r"2fa", r"otp", r"totp", r"code", r"kod", r"token", r"ověř"):
        for name in candidates:
            if re.search(pattern, name, re.I):
                return name
    if "credential_k" in candidates:
        return "credential_k"
    # Exactly one fillable empty box on a page asking for a code is unambiguous.
    return candidates[0] if len(candidates) == 1 else None


# InSIS suffixes each Zápis entry with its type, sometimes with a qualifier:
#   "Út 18:00-19:30 SB 206 (cv.)"
#   "Pá 09:15-12:30 JM 104 (cv., lichý)"
#   "Po 18:00-19:30 NB D (př.)"
_KIND_RE = re.compile(r"\s*\((cv|př|pr)\.?\s*(?:,\s*([^)]*))?\)\s*$", re.I)


def split_kind(entry: str) -> tuple[str, str]:
    """
    'Út 18:00-19:30 SB 206 (cv.)' -> ('cv', 'Út 18:00-19:30 SB 206')

    Any qualifier ('sudý', 'lichý') is kept on the text, since it changes
    which weeks the class actually runs and dropping it would mislead.
    Returns ('', entry) when there is no type marker, e.g. 'nepřiděleno'.
    """
    m = _KIND_RE.search(entry or "")
    if not m:
        return "", (entry or "").strip()
    kind = m.group(1).lower().replace("pr", "př")
    rest = entry[: m.start()].strip()
    if m.group(2):
        rest = f"{rest} ({m.group(2).strip()})"
    return kind, rest


def timetable_headers(html: str) -> list[list[str]]:
    """
    The header row of every timetable table on the page.

    Recorded in captures so that a change in column names or order between
    enrolment rounds shows up in a comparison instead of as a parse failure
    later.
    """
    soup = BeautifulSoup(html, "html.parser")
    return [_headers(tb) for tb in _find_tables(soup, TIMETABLE_HEAD)]


def page_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return " ".join(m.group(1).split()) if m else ""
