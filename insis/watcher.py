"""
The polling loop.

Three properties this has to hold, all of which have failed at some point:

  * Nothing fails silently. Every per-watch operation is wrapped, and anything
    that stops the watcher from doing its job reaches the phone.
  * A claim is verified by re-reading InSIS, never assumed from the POST
    completing. Somebody else can take the seat in between.
  * Timetable URLs are re-resolved from the enrolment page on every cycle.
    They embed the enrolment round (`akce=zap-k1`), so a URL saved in one
    round stops working in the next.
"""

import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from . import notify
from .client import SessionExpired
from .models import FREE, UNAVAILABLE
from .settings import Settings, Watch


@dataclass
class Outcome:
    watch: Watch
    slot: object | None = None
    took: bool = False
    notified: bool = False
    error: str = ""


class Watcher:
    def __init__(self, insis, settings: Settings, log=print):
        self.insis = insis
        self.settings = settings
        self.log = log
        self._last_alert: dict[str, float] = {}
        self._done: set[str] = set()
        self._warned: set[str] = set()
        # Previous state per watch, and the previous enrolment round, so that
        # changes can be reported as one digest instead of a push per class.
        self._state: dict[str, str] = {}
        self._seen: dict[str, str] = {}
        self._round: str | None = None
        self.alert_cooldown = 900

    # -- helpers -----------------------------------------------------------

    def _key(self, w: Watch) -> str:
        # Room is part of the identity: a course can run two seminars at the
        # same day and time in different rooms, and without the room they
        # would share a done-marker and an alert cooldown.
        return f"{w.course_code}|{w.day}|{w.time}|{w.room}"

    def _cooled(self, w: Watch) -> bool:
        return (time.time() - self._last_alert.get(self._key(w), 0.0)
                >= self.alert_cooldown)

    def _push(self, message: str, title: str, burst: bool = False, **kw) -> None:
        url = self.settings.ntfy_url
        if burst:
            notify.send_burst(url, message, title=title, **kw)
        else:
            notify.send(url, message, title=title, **kw)

    def _warn_once(self, key: str, message: str, title: str) -> None:
        """Send a warning the first time only, so a stuck state cannot spam."""
        if key in self._warned:
            return
        self._warned.add(key)
        self._push(message, title=title, burst=True, count=3, every=20,
                   priority="urgent", tags="warning",
                   click=self.settings.registrace_url)

    @staticmethod
    def _round_of(url: str) -> str:
        q = urlsplit(url or "").query.replace(";", "&")
        return (parse_qs(q).get("akce") or [""])[0]

    # -- resolving URLs ----------------------------------------------------

    def refresh_urls(self) -> dict:
        """
        Map course code -> current timetable URL, read from the enrolment page.

        Enrolment runs in rounds and the timetable link carries the round in
        its `akce` parameter, so a URL is only valid for the round it was read
        in. Re-reading the enrolment page each cycle means the watcher follows
        the current round without having to know how the parameter is spelled.
        """
        courses = self.insis.courses(self.settings.registrace_url)
        # Keep courses that have no timetable link too. Absent from the page
        # and present-but-unlinked are different situations: the first means
        # the course is gone, the second is what a course outside the current
        # enrolment round may look like.
        return {c.code: c for c in courses}

    def _url_for(self, w: Watch, current: dict) -> str | None:
        """
        The timetable URL to poll for this watch, or None if there is none.

        Three cases, and they must not be conflated. The course can be gone
        from the enrolment page (dropped - worth an alarm); listed but with no
        timetable link (nothing to read, but it can come back on its own, so
        it is reported quietly); or linked, in which case the link is followed
        even if it changed, because it carries the enrolment round.
        """
        course = current.get(w.course_code)

        if course is None:
            self._warn_once(
                f"gone:{w.course_code}",
                f"{w.course_code} is no longer on your enrolment page.\n"
                f"It looks like it was dropped. Nothing is being watched "
                f"for it.",
                title="InSIS - course missing")
            return None

        self._warned.discard(f"gone:{w.course_code}")

        if not course.schedule_url:
            self.log(f"  {w.course_code}: no timetable link this round")
            self._seen[self._key(w)] = UNAVAILABLE
            return None

        if course.schedule_url != w.schedule_url:
            self.log(f"  {w.course_code}: timetable link changed "
                     f"(new enrolment round?) - following it")
            w.schedule_url = course.schedule_url
            self.settings.save()
        return course.schedule_url

    # -- one watch ---------------------------------------------------------

    def check(self, w: Watch, url: str | None = None) -> Outcome:
        out = Outcome(watch=w)
        if not url:
            # Nothing to poll. Already recorded and logged by _url_for; do not
            # fall back to a URL from an earlier round, which would either
            # 404 or report stale data as if it were current.
            out.error = "no timetable link"
            return out

        slots = self.insis.slots(url)
        match = [s for s in slots if s.matches(w.day, w.time, w.room, w.kind)]
        if not match:
            out.error = (f"no slot matching {w.day!r} {w.time!r} "
                         f"{w.room!r}".rstrip())
            self.log(f"  {w.course_code}: {out.error}")
            self._warn_once(
                f"missing:{self._key(w)}",
                f"{w.course_code} {w.day} {w.time} {w.room} is no longer in "
                f"the timetable. The class may have been cancelled or moved.",
                title="InSIS - slot missing")
            return out

        self._warned.discard(f"missing:{self._key(w)}")
        if len(match) > 1:
            self.log(f"  {w.course_code}: {len(match)} rows match "
                     f"{w.day} {w.time} {w.room} - using the first")

        slot = match[0]
        out.slot = slot
        self._seen[self._key(w)] = slot.state

        if slot.selected:
            self.log(f"  {w.course_code}: {slot.label()} is already yours")
            self._done.add(self._key(w))
            out.took = True
            self._push(
                f"{w.course_name or w.course_code}\n"
                f"{slot.label()} is already enrolled.",
                title="InSIS - you have the slot", burst=True, count=3,
                every=15, priority="urgent", tags="white_check_mark",
                click=self.settings.registrace_url)
            return out

        self.log(f"  {w.course_code}: {slot.label()} - {slot.seats} - "
                 f"{slot.state}")

        if not slot.takeable:
            return out

        if not w.take:
            if self._cooled(w):
                self._last_alert[self._key(w)] = time.time()
                out.notified = True
                self._push(
                    f"FREE: {w.course_name or w.course_code}\n"
                    f"{slot.label()} - {slot.seats}\n{slot.note}",
                    title="InSIS - seat free", burst=True, count=5, every=20,
                    priority="urgent", click=url)
            return out

        return self._claim(w, slot, url, out)

    def _claim(self, w: Watch, slot, url: str, out: Outcome) -> Outcome:
        try:
            self.insis.take(url, slot.action_id)
        except SessionExpired:
            raise
        except Exception as e:
            out.error = f"claim failed: {type(e).__name__}: {e}"
            self.log(f"  {out.error}")
            self._push(
                f"{w.course_name or w.course_code} {slot.label()}\n"
                f"A seat opened but the enrolment failed:\n{e}\n"
                f"Still watching.",
                title="InSIS - enrolment failed", priority="urgent",
                tags="warning", click=url)
            return out

        try:
            after = self.insis.slots(url)
            got = [s for s in after
                   if s.matches(w.day, w.time, w.room, w.kind)]
            out.took = bool(got and got[0].selected)
        except Exception as e:
            # The claim may well have worked; we just cannot see it right now.
            out.error = f"could not verify: {e}"
            self.log(f"  {out.error}")

        if out.took:
            self._done.add(self._key(w))
            self.log(f"  {w.course_code}: CLAIMED {slot.label()}")
            self._push(
                f"ENROLLED: {w.course_name or w.course_code}\n{slot.label()}",
                title="InSIS - you have the slot", burst=True, count=3,
                every=15, priority="urgent", tags="white_check_mark",
                click=self.settings.registrace_url)
        else:
            self.log(f"  {w.course_code}: claim not confirmed")
            self._push(
                f"{w.course_name or w.course_code} {slot.label()}\n"
                f"Tried to enrol but could not confirm it. Check InSIS.\n"
                f"Still watching.",
                title="InSIS - not confirmed", priority="urgent",
                tags="warning", click=self.settings.registrace_url)
        return out

    def _digest(self, watches: list[Watch], round_now: str) -> None:
        """
        One calm push summarising changes that are not individually urgent.

        A slot becoming FREE already sends its own urgent alert, so it is left
        out here; what this covers is the quieter half - a round opening or
        closing, and slots locking or filling as a result. Sending one message
        per class would be unreadable when a round flips several at once.
        """
        lines: list[str] = []

        if self._round and round_now and round_now != self._round:
            lines.append(f"Enrolment round changed: "
                         f"{self._round} -> {round_now}")

        for w in watches:
            key = self._key(w)
            now = self._seen.get(key)
            before = self._state.get(key)
            if now is None or before is None or now == before:
                continue
            if now == FREE:
                continue        # already alerted, urgently
            lines.append(f"{w.course_code} {w.day} {w.time} {w.room}: "
                         f"{before} -> {now}")

        # Suppressed only when there is nothing to compare against, i.e. the
        # very first cycle. Keying this on the round instead would swallow the
        # digest whenever the first cycle happened to see no timetable links.
        if not self._state or not lines:
            return

        self._push("\n".join(lines),
                   title="InSIS - status changed", priority="default",
                   tags="information_source",
                   click=self.settings.registrace_url)

    # -- loop --------------------------------------------------------------

    def run(self, once: bool = False) -> int:
        watches = list(self.settings.watches)
        if not watches:
            self.log("Nothing on the watch list.")
            return 1

        self.log(f"Watching {len(watches)} slot(s), every "
                 f"{self.settings.poll_seconds}s. Ctrl-C to stop.")
        for w in watches:
            self.log(f"  - {w.describe()}")

        while True:
            pending = [w for w in watches if self._key(w) not in self._done]
            if not pending:
                self.log("Every watched slot is enrolled - done.")
                return 0

            self.log(time.strftime("[%H:%M:%S] poll"))
            self._seen: dict[str, str] = {}

            # None means the page could not be read at all, which is not the
            # same as reading it and finding no courses: the first keeps the
            # stored URLs, the second is worth warning about.
            try:
                current = self.refresh_urls()
                self._warned.discard("enrolment-page")
            except SessionExpired:
                return self._session_died()
            except Exception as e:
                self.log(f"  could not read the enrolment page: {e}")
                self._warn_once(
                    "enrolment-page",
                    f"Cannot read your enrolment page ({e}).\n"
                    f"Still trying the last known timetable links.",
                    title="InSIS - enrolment page unreadable")
                current = None

            if current is not None and not current:
                # Every course disappearing at once is far more likely to be
                # the page changing shape than an actual mass unenrolment.
                # Treating it as "all dropped" would fire an urgent alarm per
                # watch and stop polling; treat it as unreadable instead and
                # keep using the links we already have.
                self.log("  enrolment page parsed to zero courses - "
                         "treating it as unreadable")
                self._warn_once(
                    "enrolment-page",
                    "Your enrolment page loaded but no courses could be read "
                    "from it.\nThe page layout may have changed. Still trying "
                    "the last known timetable links.",
                    title="InSIS - enrolment page unreadable")
                current = None

            for w in pending:
                try:
                    url = (self._url_for(w, current) if current is not None
                           else w.schedule_url)
                    if url:
                        self.check(w, url)
                except SessionExpired:
                    return self._session_died()
                except Exception as e:
                    self.log(f"  {w.course_code}: poll error "
                             f"{type(e).__name__}: {e}")
                    self._warn_once(
                        f"poll:{self._key(w)}",
                        f"Cannot check {w.course_code} {w.day} {w.time}:\n{e}\n"
                        f"It is not being watched properly.",
                        title="InSIS - check failing")

            round_now = ""
            if current:
                round_now = next(
                    (self._round_of(c.schedule_url) for c in current.values()
                     if c.schedule_url), "")
            self._digest(pending, round_now)
            self._state.update(self._seen)
            if round_now:
                self._round = round_now

            if once:
                return 0
            time.sleep(self.settings.poll_seconds)

    def _session_died(self) -> int:
        self.log("SESSION EXPIRED - cannot log back in unattended.")
        self._push(
            "Your InSIS session expired and I cannot log in again on my own "
            "(the password is never stored).\n"
            "Start the app again, or nothing is being watched.",
            title="InSIS - watcher stopped", burst=True, count=3, every=20,
            priority="urgent", tags="rotating_light",
            click=self.settings.registrace_url)
        return 2
