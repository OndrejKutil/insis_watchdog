"""
The full-screen front end.

The app takes over the terminal and redraws everything on every keystroke, so
what is on screen is always the current state -- no scrollback to wade through
and no half-stale output. It runs in the alternate screen buffer, so quitting
gives the terminal back exactly as it was.

Single keypresses drive everything, so there is no permanent prompt line.
Only free text (an ntfy topic, a password) falls back to a line editor.
"""

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

from . import keys, notify, parse, screen
from .client import Insis, InsisError, LoginFailed, SessionExpired
from .models import FREE, FULL, LOCKED, YOURS
from .screen import (CYAN, DIM, GREEN, MAGENTA, RED, RESET, REVERSE,
                     YELLOW, fit, pad)
from .settings import (MOJE_STUDIUM, REGISTRACE_NEEDLE, STATE_FILE,
                       Settings, Watch)
from .watcher import Watcher

DEBUG_DIR = Path(__file__).resolve().parent.parent / "captures" / "debug"
TITLE = "InSIS slot picker"

# cv is the one you can usually change; př is fixed. Distinct hues rather than
# reusing the green/cyan that mean YOURS/FREE on the timetable screen.
KIND_COLOURS = {"cv": CYAN, "př": MAGENTA}

# LOCKED is deliberately not red: seats are free and it may open by itself
# when the enrolment round for that class starts.
STATE_COLOURS = {YOURS: CYAN, FREE: GREEN, LOCKED: YELLOW, FULL: DIM}


def dump_debug(course, html: str) -> Path:
    """
    Save a page that failed to parse, so the layout can be diagnosed.

    Pages behind the login cannot be fetched any other way. The file contains
    the student's name and study id, so treat it as personal data.
    """
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    path = DEBUG_DIR / f"{course.code}-{stamp}.html"
    path.write_text(html, encoding="utf-8")
    (DEBUG_DIR / f"{course.code}-{stamp}.url.txt").write_text(
        course.schedule_url or "", encoding="utf-8")
    return path


class App:
    def __init__(self, settings: Settings, insis: Insis):
        self.settings = settings
        self.insis = insis
        self.courses = []
        self.course = None          # course whose timetable is on screen
        self.slots = []
        self.view = "courses"
        self.prev_view = "courses"
        # One cursor per list view. Arrow keys move it, Enter activates.
        # Digits alone cannot work past nine rows: '1' is ambiguous as soon
        # as a second digit could follow.
        self.cursor = {"courses": 0, "slots": 0, "watchlist": 0, "help": 0}
        self.message = ""
        self.error = ""

    # -- data --------------------------------------------------------------

    def load_courses(self) -> bool:
        try:
            self.courses = self.insis.courses(self.settings.registrace_url)
            return True
        except SessionExpired:
            self.error = "Session expired — restart and log in again."
            return False
        except InsisError as e:
            self.error = f"Could not load courses: {e}"
            return False

    def load_slots(self, course) -> bool:
        try:
            html = self.insis.get_html(course.schedule_url)
        except SessionExpired:
            self.error = "Session expired — restart and log in again."
            raise
        except InsisError as e:
            self.message = f"{RED}{e}{RESET}"
            return False

        self.slots = parse.parse_slots(html)
        if not self.slots:
            path = dump_debug(course, html)
            self.message = (f"{RED}No timetable rows on that page "
                            f"({parse.page_title(html)!r}) — saved "
                            f"{path.name}{RESET}")
            return False
        self.course = course
        return True

    def rows(self, view: str = None) -> list:
        view = view or self.view
        if view == "help":
            # Not selectable rows -- just something for move() to bound
            # against, so ↑↓ scroll the text instead of doing nothing.
            return HELP_TEXT.strip("\n").splitlines()
        return {"courses": self.courses, "slots": self.slots,
                "watchlist": self.settings.watches}.get(view, [])

    def move(self, delta: int) -> None:
        items = self.rows()
        if not items:
            return
        cur = self.cursor.get(self.view, 0)
        self.cursor[self.view] = max(0, min(len(items) - 1, cur + delta))

    def selected_index(self) -> int:
        items = self.rows()
        if not items:
            return -1
        return max(0, min(len(items) - 1, self.cursor.get(self.view, 0)))

    def _first_interesting(self) -> int:
        """Start the cursor on something takeable rather than on row one."""
        for i, s in enumerate(self.slots):
            if s.takeable:
                return i
        return 0

    def marker(self, i: int) -> tuple[str, str]:
        """(prefix, colour) for row i, highlighting the cursor row."""
        if self.view == "help":
            return " ", ""
        if i == self.selected_index():
            return "›", REVERSE
        return " ", ""

    # -- views -------------------------------------------------------------

    def view_courses(self):
        """Returns (title, body, footer, focus-line-index)."""
        total = screen.width() - 2
        w_num, w_code = 3, 9
        # Course names are long but rarely need reading in full; the times
        # are the point of this screen, so they get first claim on the width.
        w_kind = 3
        w_zapis = min(40, max(26, total - w_num - w_code - w_kind - 30 - 6))
        w_name = max(18, total - w_num - w_code - w_kind - w_zapis - 6)

        body = [
            f"  {DIM}{'#':<{w_num}} {'CODE':<{w_code}} "
            f"{fit('COURSE', w_name)} {'TYP':<{w_kind}} ENROLLED{RESET}",
            screen.rule(),
        ]
        focus = None
        # One line per enrolled time. A course with a lecture and a seminar
        # has two, and InSIS shows both.
        indent = " " * (w_num + 1 + w_code + 1 + w_name)
        for i, c in enumerate(self.courses):
            colour = "" if c.assigned else YELLOW
            cur, hl = self.marker(i)
            if hl:
                focus = len(body)
            for j, (kind, when) in enumerate(c.zapis_rows):
                if j == 0:
                    left = (f"{i+1:<{w_num}} {c.code:<{w_code}} "
                            f"{fit(c.name, w_name)}")
                    mark = " " if c.assigned else "!"
                    prefix = cur
                else:
                    left, mark, prefix = indent, " ", " "

                # Colour the type, not the whole row: it is the one field you
                # scan for, and a seminar is what you can actually change.
                kind_txt = f"{kind:<{w_kind}}"
                kind_col = KIND_COLOURS.get(kind, "")
                body_txt = f"{mark}{fit(when, w_zapis).rstrip()}"

                if hl:
                    line = f"{left} {kind_txt} {body_txt}"
                    body.append(f" {prefix}{hl}{pad(' ' + line, total - 1)}"
                                f"{RESET}")
                else:
                    body.append(f" {prefix}{colour} {left}{RESET} "
                                f"{kind_col}{kind_txt}{RESET} "
                                f"{colour}{body_txt}{RESET}")

        missing = [c for c in self.courses if not c.assigned]
        body.append("")
        if missing:
            body.append(f"  {YELLOW}! no place assigned yet: "
                        f"{', '.join(c.code for c in missing)}{RESET}")
        else:
            body.append(f"  {GREEN}every course has an assigned place.{RESET}")

        footer = [
            f"{CYAN}↑↓{RESET} move   {CYAN}[Enter]{RESET} open course   "
            f"{CYAN}[w]{RESET} watch list   {CYAN}[s]{RESET} start watching",
            f"{CYAN}[n]{RESET} ntfy topic   {CYAN}[r]{RESET} refresh   "
            f"{CYAN}[h]{RESET} help   {CYAN}[q]{RESET} quit",
        ]
        return TITLE, body, footer, focus

    def view_slots(self):
        total = screen.width() - 2
        w_num, w_day, w_time, w_cap = 3, 9, 13, 9
        spare = total - (w_num + w_day + w_time + w_cap + 6 + 7)
        w_room = max(10, int(spare * 0.5))
        w_teach = max(9, spare - w_room)

        body = [
            f"  {self.course.code} — {self.course.name}",
            "",
        ]
        focus = None
        header = (f"{'#':<{w_num}} {'DAY':<{w_day}} {'TIME':<{w_time}} "
                  f"{fit('ROOM', w_room)} {fit('TEACHER', w_teach)} "
                  f"{'SEATS':<{w_cap}} STATUS")

        # A course can have a lecture and a seminar; they are separate choices
        # with separate radio groups, so they get separate blocks rather than
        # being run together into one undifferentiated list.
        last_kind = None
        for i, s in enumerate(self.slots):
            if s.kind != last_kind:
                if last_kind is not None:
                    body.append("")
                body.append(f"  {CYAN}{s.kind or 'Rozvrh'}{RESET}")
                body.append(f"  {DIM}{header}{RESET}")
                body.append(screen.rule())
                last_kind = s.kind

            state = s.state
            colour = STATE_COLOURS.get(state, DIM)
            cur, hl = self.marker(i)
            if hl:
                focus = len(body)
            line = (f"{i+1:<{w_num}} {s.day:<{w_day}} {s.time:<{w_time}} "
                    f"{fit(s.room, w_room)} {fit(s.teacher, w_teach)} "
                    f"{s.seats:<{w_cap}} {state}")
            if hl:
                body.append(f" {cur}{hl}{pad(' ' + line, total - 1)}{RESET}")
            else:
                body.append(f" {cur} {line[:-len(state)]}{colour}{state}{RESET}")

        notes = [s for s in self.slots if s.note]
        if notes:
            body.append("")
            for s in notes[:8]:
                body.append(f"    {DIM}{s.day} {s.time}: "
                            f"{fit(s.note, total - 26).rstrip()}{RESET}")

        footer = [f"{CYAN}↑↓{RESET} move   {CYAN}[Enter]{RESET} watch this slot"
                  f"   {CYAN}[b]{RESET} back   {CYAN}[h]{RESET} help   "
                  f"{CYAN}[q]{RESET} quit"]
        return f"{TITLE} — {self.course.code}", body, footer, focus

    def view_watchlist(self):
        body = []
        focus = None
        if not self.settings.watches:
            body.append(f"  {DIM}Watch list is empty. Open a course and pick "
                        f"a slot.{RESET}")
        else:
            body.append(f"  {DIM}Checked every {self.settings.poll_seconds}s "
                        f"once watching starts.{RESET}")
            body.append("")
            for i, w in enumerate(self.settings.watches):
                mode = "auto-take" if w.take else "notify only"
                cur, hl = self.marker(i)
                if hl:
                    focus = len(body)
                line = f"{i+1}. {w.course_code:<9} {w.day} {w.time}   {mode}"
                if hl:
                    body.append(f" {cur}{hl}{pad(' ' + line, 60)}{RESET}")
                else:
                    colour = GREEN if w.take else YELLOW
                    body.append(f" {cur} {line.replace(mode, colour+mode+RESET)}")
        footer = [f"{CYAN}↑↓{RESET} move   {CYAN}[d]{RESET} remove   "
                  f"{CYAN}[s]{RESET} start watching   {CYAN}[b]{RESET} back   "
                  f"{CYAN}[q]{RESET} quit"]
        return f"{TITLE} — watch list", body, footer, focus

    def view_help(self):
        body = [f"  {line}" for line in HELP_TEXT.strip("\n").splitlines()]
        body.append("")
        body.append(f"  {DIM}state file: {STATE_FILE}{RESET}")
        body.append(f"  {DIM}ntfy topic: "
                    f"{self.settings.ntfy_topic or '(not set — press n)'}"
                    f"{RESET}")
        return (f"{TITLE} — help", body,
                [f"{CYAN}↑↓{RESET} scroll   {CYAN}[b]{RESET} back   "
                 f"{CYAN}[q]{RESET} quit"],
                self.cursor.get("help", 0))

    # -- rendering ---------------------------------------------------------

    def status(self) -> str:
        topic = "ntfy ✓" if self.settings.ntfy_topic else "no ntfy"
        return (f"{self.settings.username or '?'} · {topic} · "
                f"{len(self.settings.watches)} watched ")

    def draw(self) -> None:
        title, body, footer, focus = {
            "courses": self.view_courses,
            "slots": self.view_slots,
            "watchlist": self.view_watchlist,
            "help": self.view_help,
        }[self.view]()
        screen.render(title, body, footer, status=self.status(),
                      message=self.message, focus=focus)
        self.message = ""

    # -- interaction -------------------------------------------------------

    def choose_mode(self, slot) -> str | None:
        self.message = (f"{slot.label()} — {slot.seats}    "
                        f"{CYAN}[n]{RESET} notify me    "
                        f"{CYAN}[t]{RESET} take it automatically    "
                        f"{CYAN}[Esc]{RESET} cancel")
        self.draw()
        while True:
            try:
                k = keys.read_key()
            except (KeyboardInterrupt, EOFError):
                return None
            if k in ("n", "t"):
                return k
            if k in ("ESC", "b", "ENTER", "q"):
                return None

    def add_watch(self, slot) -> None:
        mode = self.choose_mode(slot)
        if mode is None:
            self.message = "cancelled."
            return
        w = Watch(
            course_code=self.course.code,
            course_name=self.course.name,
            schedule_url=self.course.schedule_url,
            day=slot.day, time=slot.time, room=slot.room, kind=slot.kind,
            take=(mode == "t"),
        )
        if self.settings.add_watch(w):
            self.settings.save()
            self.message = f"{GREEN}added: {w.describe()}{RESET}"
        else:
            self.message = "already on the watch list."

    def edit_ntfy(self) -> None:
        screen.render(f"{TITLE} — notifications", [
            "  Notifications go to an ntfy.sh topic. Install the ntfy app on",
            "  your phone and subscribe to the same name.",
            "",
            f"  {YELLOW}Anyone who knows the topic can read your alerts and "
            f"post to it,{RESET}",
            f"  {YELLOW}so pick something unguessable.{RESET}",
            "",
            f"  current: {self.settings.ntfy_topic or '(none)'}",
        ], [f"{DIM}Enter alone keeps the current value{RESET}"], self.status())

        topic = screen.prompt_line("ntfy topic")
        if not topic:
            self.message = "unchanged."
            return
        self.settings.ntfy_topic = topic
        self.settings.save()
        ok = notify.send(
            self.settings.ntfy_url,
            "Test from insis-picker. If you can read this, it works.",
            title="insis-picker - test", priority="default",
            tags="white_check_mark")
        self.message = (f"{GREEN}saved, test push sent.{RESET}" if ok else
                        f"{RED}saved, but the test push failed.{RESET}")

    def start_watching(self) -> int:
        """Returns a process exit code, or -1 to stay in the UI."""
        if not self.settings.watches:
            self.message = "Nothing to watch yet — open a course, pick a slot."
            return -1
        if not self.settings.ntfy_topic:
            self.message = (f"{YELLOW}No ntfy topic set — you would not hear "
                            f"about it. {CYAN}[n]{RESET}{YELLOW} set one, "
                            f"any other key to go anyway.{RESET}")
            self.draw()
            if keys.read_key() == "n":
                self.edit_ntfy()
                return -1

        lines: list[str] = []

        def log(msg: str) -> None:
            lines.append(str(msg))
            rows = max(5, screen.size()[1] - 8)
            screen.render(
                f"{TITLE} — watching",
                [f"  {ln}" for ln in lines[-rows:]],
                [f"{DIM}Ctrl-C to stop. Your phone gets a push either way."
                 f"{RESET}"],
                status=self.status())

        try:
            return Watcher(self.insis, self.settings, log=log).run()
        except KeyboardInterrupt:
            self.message = "stopped watching."
            return -1

    # -- main loop ---------------------------------------------------------

    def run(self) -> int:
        if not self.load_courses():
            return 1

        while True:
            self.draw()
            try:
                k = keys.read_key()
            except (KeyboardInterrupt, EOFError):
                return 0

            if k == "q":
                return 0

            # Help works from anywhere and returns you where you were, so it
            # is never a dead end you have to guess your way out of.
            if k == "h" and self.view != "help":
                self.prev_view = self.view
                self.cursor["help"] = 0
                self.view = "help"
                continue

            # Movement is global: same keys in every list.
            if k in ("UP", "k"):
                self.move(-1)
                continue
            if k in ("DOWN", "j"):
                self.move(1)
                continue
            if k.isdigit() and self.view in self.cursor:
                # Digits jump the cursor rather than activating, so '1' on a
                # ten-row list is a move you can see and correct, not a
                # mis-selection you only notice afterwards.
                n = int(k)
                if 1 <= n <= len(self.rows()):
                    self.cursor[self.view] = n - 1
                continue

            try:
                rc = self.handle(k)
            except SessionExpired:
                return 2
            if rc is not None:
                return rc

    def handle(self, k: str) -> int | None:
        if self.view == "courses":
            if k == "r":
                if not self.load_courses():
                    return 1
                self.message = "reloaded."
            elif k == "w":
                self.view = "watchlist"
            elif k == "n":
                self.edit_ntfy()
            elif k == "s":
                rc = self.start_watching()
                if rc >= 0:
                    return rc
            elif k in ("ENTER", "RIGHT"):
                i = self.selected_index()
                if i < 0:
                    return None
                course = self.courses[i]
                if not course.choosable:
                    self.message = "That course has no timetable link."
                elif self.load_slots(course):
                    self.cursor["slots"] = self._first_interesting()
                    self.view = "slots"

        elif self.view == "slots":
            if k in ("b", "LEFT", "ESC"):
                self.view = "courses"
            elif k in ("ENTER", "RIGHT"):
                i = self.selected_index()
                if i < 0:
                    return None
                slot = self.slots[i]
                if slot.selected:
                    self.message = "That one is already yours."
                else:
                    self.add_watch(slot)

        elif self.view == "watchlist":
            if k in ("b", "LEFT", "ESC"):
                self.view = "courses"
            elif k == "s":
                rc = self.start_watching()
                if rc >= 0:
                    return rc
            elif k in ("d", "DELETE"):
                i = self.selected_index()
                if 0 <= i < len(self.settings.watches):
                    gone = self.settings.watches[i]
                    self.settings.remove_watch(gone)
                    self.settings.save()
                    self.cursor["watchlist"] = max(0, i - 1)
                    self.message = f"removed: {gone.describe()}"

        elif self.view == "help":
            if k in ("b", "ESC", "h"):
                self.view = self.prev_view
        return None


HELP_TEXT = """
What it does
  Watches a course timetable for a seat to free up, and either tells you or
  claims it for you. InSIS hands out places in rounds; if a class was full
  when your round opened, the only way in is for someone to drop out.

Keys
  arrow up/down  move the cursor (k and j work too)
  Enter          open the selected course, or watch the selected slot
  b / left       back
  1-9            jump the cursor to that row - it does not select, so a
                 mistyped digit is a visible move you can correct
  d              remove the selected entry from the watch list
  w              watch list        s   start watching     n   set ntfy topic
  r              reload from InSIS h   this help          q   quit

  Commands need no Enter; only free text (the ntfy topic, your password)
  waits for it.

The course list
  ENROLLED is the time you actually got. A yellow '!' means nothing has been
  assigned yet. The TYP column keeps InSIS's own labels: cv is a seminar
  (cvičení), př is a lecture (přednáška).

A timetable
  FREE    a seat is free and InSIS will let you take it
  FULL    every seat is taken
  LOCKED  seats are free, but InSIS offers no way to select them - normally
          a class whose enrolment round has not opened yet
  YOURS   this one is already yours

Enrolment rounds
  Enrolment runs in rounds, one class at a time, and a course outside the
  current round shows an empty ENROLLED column and a LOCKED timetable. That
  is expected, not a fault. Add the watch anyway: when the round opens, the
  slot becomes FREE and the watcher acts on it.

  The timetable link carries the round in it, so it changes between rounds.
  The watcher re-reads the enrolment page every cycle and follows the new
  link by course code, and tells you if a course disappears.

Watching
  Per slot you choose 'notify me' or 'take it automatically'. Auto-take
  claims the seat and then verifies it against InSIS rather than assuming
  the POST worked. Either way your phone gets a push.

Unattended, e.g. on a Raspberry Pi
  uv run main.py --watch     log in, then watch the saved list
  uv run main.py --list      print courses and exit

  Credentials are never written to disk, so if InSIS expires the session the
  watcher cannot log back in. It sends an urgent push and stops, rather than
  pretending to still be watching.
"""


# -- entry -------------------------------------------------------------------

def do_login(settings: Settings) -> Insis:
    """Runs before the full-screen UI starts, so failures stay readable."""
    print()
    print(f"  {TITLE}")
    print("  Nothing is stored: your password lives in memory for this run only.")
    print()
    insis = Insis()
    try:
        prompt = f"  username [{settings.username}]: " if settings.username \
            else "  username: "
        username = input(prompt).strip() or settings.username
        password = getpass.getpass("  password (hidden): ")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130)

    if not username or not password:
        print("\n  Need both a username and a password.")
        raise SystemExit(1)

    def ask_code() -> str:
        print("\n  InSIS wants your 6-digit authenticator code.")
        try:
            return input("  code: ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    print("\n  logging in...")
    insis.login(username, password, get_code=ask_code,
                destination=settings.registrace_url or None)
    if username != settings.username:
        settings.username = username
        settings.save()
    return insis


def first_run_setup(settings: Settings, insis: Insis) -> bool:
    """
    Work out which enrollment page belongs to this student.

    The URL embeds studium and obdobi ids that are unique to the person and
    the semester, so it cannot be shipped as a default. 'Moje studium' links
    to it; if that fails, or the student has more than one study, ask.
    """
    print("\n  First run — finding your enrollment page...")
    try:
        found = insis.find_registrace_urls(MOJE_STUDIUM)
    except InsisError as e:
        print(f"  (could not read 'Moje studium': {e})")
        found = []

    if len(found) == 1:
        settings.registrace_url = found[0][1]
        settings.save()
        print(f"  found: {found[0][0]}")
        return True

    if len(found) > 1:
        print("\n  You have more than one. Which do you want to watch?\n")
        for i, (label, url) in enumerate(found, 1):
            print(f"    {i}. {label}")
        try:
            pick = input("\n  number: ").strip()
        except (EOFError, KeyboardInterrupt):
            return False
        if pick.isdigit() and 1 <= int(pick) <= len(found):
            settings.registrace_url = found[int(pick) - 1][1]
            settings.save()
            return True
        print("  no such option.")
        return False

    # Nothing found -- fall back to pasting it, which always works.
    print("""
  Could not find it automatically. Open InSIS in a browser, go to
  Moje studium -> Portál studenta -> Registrace/Zápis předmětů, and copy
  the address from the URL bar. It looks like:

    https://insis.vse.cz/auth/student/registrace.pl?studium=NNNNNN;obdobi=NNNN;lang=cz
""")
    try:
        url = input("  paste it here: ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if REGISTRACE_NEEDLE not in url:
        print("  that does not look like the enrollment page.")
        return False
    settings.registrace_url = url
    settings.save()
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="insis", description="Watch InSIS for a free timetable slot.")
    ap.add_argument("--watch", action="store_true",
                    help="skip the UI: log in and watch the saved list")
    ap.add_argument("--once", action="store_true",
                    help="with --watch: check once and exit")
    ap.add_argument("--list", action="store_true",
                    help="print your courses and exit")
    ap.add_argument("--every", type=int, help="seconds between polls")
    ap.add_argument("--no-fullscreen", action="store_true",
                    help="plain scrolling output instead of taking the screen")
    args = ap.parse_args(argv)

    settings = Settings.load()
    if args.every:
        settings.poll_seconds = args.every

    try:
        insis = do_login(settings)
    except LoginFailed as e:
        print(f"\n  Login failed: {e}\n")
        return 1
    except InsisError as e:
        print(f"\n  Could not reach InSIS: {e}\n")
        return 1

    if not settings.registrace_url and not first_run_setup(settings, insis):
        print("\n  Cannot continue without the enrollment page URL.\n")
        return 1

    if args.list:
        for c in insis.courses(settings.registrace_url):
            flag = " " if c.assigned else "!"
            print(f"  {flag}{c.code:<9} {c.name[:40]:<42} {c.zapis_short}")
        return 0

    if args.watch:
        if not settings.watches:
            print("  Watch list is empty — run without --watch and add one.")
            return 1
        return Watcher(insis, settings).run(once=args.once)

    app = App(settings, insis)
    with screen.FullScreen(active=not args.no_fullscreen):
        rc = app.run()
    if app.error:
        print(f"\n  {app.error}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
