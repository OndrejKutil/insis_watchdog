"""
Save a snapshot of your InSIS enrolment pages, and compare two snapshots.

Enrolment runs in rounds, and each round shows different pages: courses whose
round has not opened have no selectable times, and the timetable links carry a
different `akce` parameter. To handle a round we have not seen, we need a
capture of it next to a capture of one we have.

    uv run capture.py                      # snapshot the current round
    uv run capture.py --list               # list snapshots taken so far
    uv run capture.py --compare A B        # what changed between two of them

Every run writes a new timestamped directory under captures/, so running it
again in a later round never overwrites what came before.

    captures/
      round-zap-k1-20260907-174500/
        manifest.json      structured summary, used by --compare
        registrace.html    the enrolment page
        TVSVOL.html        one file per course timetable
        ...

These files contain your name, study id and course list. captures/ is
gitignored; keep it that way, and check any file before sharing it.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from insis import parse
from insis.cli import do_login, first_run_setup
from insis.client import InsisError
from insis.settings import Settings

CAPTURES = Path(__file__).resolve().parent / "captures"


def round_of(url: str) -> str:
    """The enrolment round from a timetable URL, e.g. 'zap-k1'."""
    if not url:
        return "unknown"
    q = urlsplit(url).query.replace(";", "&")
    return (parse_qs(q).get("akce") or ["unknown"])[0]


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "unnamed"


def slot_row(s) -> dict:
    return {
        "day": s.day, "time": s.time, "kind": s.kind, "room": s.room,
        "teacher": s.teacher, "seats": s.seats, "state": s.state,
        "action_id": s.action_id, "group_name": s.group_name,
        "selected": s.selected,
    }


def capture(settings: Settings, insis) -> Path:
    reg_html = insis.get_html(settings.registrace_url)
    courses = parse.parse_courses(reg_html, base=settings.registrace_url)

    akce = next((round_of(c.schedule_url) for c in courses if c.schedule_url),
                "unknown")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = CAPTURES / f"round-{safe(akce)}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    (out / "registrace.html").write_text(reg_html, encoding="utf-8")

    manifest = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "round": akce,
        "registrace_url": settings.registrace_url,
        "page_title": parse.page_title(reg_html),
        "courses": [],
    }

    print(f"\n  round {akce}, {len(courses)} courses -> {out.name}\n")
    for c in courses:
        entry = {
            "code": c.code, "name": c.name, "klass": c.klass,
            "group": c.group, "url": c.schedule_url,
            "round": round_of(c.schedule_url), "zapis": c.zapis_parts,
            "assigned": c.assigned, "slots": [], "error": None,
        }

        if not c.schedule_url:
            entry["error"] = "no timetable link"
            print(f"    {c.code:<9} (no timetable link)")
        else:
            try:
                html = insis.get_html(c.schedule_url)
                (out / f"{safe(c.code)}.html").write_text(html, encoding="utf-8")
                slots = parse.parse_slots(html)
                entry["slots"] = [slot_row(s) for s in slots]
                entry["page_title"] = parse.page_title(html)
                entry["headers"] = parse.timetable_headers(html)
                states = ", ".join(
                    f"{n}x{st}" for st, n in
                    sorted({s.state: sum(1 for x in slots if x.state == s.state)
                            for s in slots}.items()))
                print(f"    {c.code:<9} {len(slots):>2} slots  {states}")
            except InsisError as e:
                entry["error"] = str(e)
                print(f"    {c.code:<9} ERROR: {e}")

        manifest["courses"].append(entry)

    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# -- comparing ---------------------------------------------------------------

def load(path: str) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "manifest.json"
    if not p.exists():
        raise SystemExit(f"  no manifest at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def slot_key(s: dict) -> str:
    return f"{s['kind']}|{s['day']}|{s['time']}|{s['room']}"


def compare(a: dict, b: dict) -> None:
    print(f"\n  A  {a['round']}  {a['captured_at']}")
    print(f"  B  {b['round']}  {b['captured_at']}")
    print()

    if a["round"] != b["round"]:
        print(f"  ROUND CHANGED: {a['round']} -> {b['round']}")
        print("  Timetable URLs differ between rounds, which is why the")
        print("  watcher re-resolves them from the enrolment page.\n")

    ca = {c["code"]: c for c in a["courses"]}
    cb = {c["code"]: c for c in b["courses"]}

    gone = sorted(set(ca) - set(cb))
    new = sorted(set(cb) - set(ca))
    if gone:
        print(f"  courses only in A: {', '.join(gone)}")
    if new:
        print(f"  courses only in B: {', '.join(new)}")

    for code in sorted(set(ca) & set(cb)):
        x, y = ca[code], cb[code]
        lines = []

        if x["zapis"] != y["zapis"]:
            lines.append(f"    ENROLLED: {x['zapis'] or '-'} -> "
                         f"{y['zapis'] or '-'}")
        if x.get("headers") != y.get("headers"):
            lines.append(f"    columns changed: {x.get('headers')} -> "
                         f"{y.get('headers')}")
        if x.get("error") != y.get("error"):
            lines.append(f"    error: {x.get('error')} -> {y.get('error')}")

        sa = {slot_key(s): s for s in x["slots"]}
        sb = {slot_key(s): s for s in y["slots"]}
        for k in sorted(set(sa) - set(sb)):
            lines.append(f"    slot gone: {k}")
        for k in sorted(set(sb) - set(sa)):
            lines.append(f"    slot new:  {k}  [{sb[k]['state']}]")
        for k in sorted(set(sa) & set(sb)):
            p, q = sa[k], sb[k]
            if p["state"] != q["state"]:
                lines.append(f"    {k}: {p['state']} -> {q['state']}"
                             f"   seats {p['seats']} -> {q['seats']}")
            elif p["seats"] != q["seats"]:
                lines.append(f"    {k}: seats {p['seats']} -> {q['seats']}")
            if p["group_name"] != q["group_name"]:
                lines.append(f"    {k}: radio group {p['group_name']} -> "
                             f"{q['group_name']}")

        if lines:
            print(f"\n  {code} — {x['name']}")
            for line in lines:
                print(line)

    print()


def list_captures() -> None:
    dirs = sorted(d for d in CAPTURES.glob("round-*") if d.is_dir())
    if not dirs:
        print("\n  No snapshots yet. Run: uv run capture.py\n")
        return
    print()
    for d in dirs:
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            n = len(m["courses"])
            slots = sum(len(c["slots"]) for c in m["courses"])
            print(f"  {d.name:<38} round {m['round']:<8} "
                  f"{n} courses, {slots} slots")
        except (OSError, json.JSONDecodeError, KeyError):
            print(f"  {d.name:<38} (unreadable manifest)")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list snapshots")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"),
                    help="compare two snapshot directories")
    args = ap.parse_args()

    if args.list:
        list_captures()
        return 0

    if args.compare:
        compare(load(args.compare[0]), load(args.compare[1]))
        return 0

    settings = Settings.load()
    insis = do_login(settings)
    if not settings.registrace_url and not first_run_setup(settings, insis):
        print("\n  Cannot continue without the enrolment page URL.\n")
        return 1

    out = capture(settings, insis)
    print(f"\n  Saved to {out}")
    print("  Run this again when the next round opens, then:")
    print(f"    uv run capture.py --compare {out.name} <the-new-one>\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
