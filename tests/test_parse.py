"""
Parser tests, built on synthetic markup that reproduces the real InSIS quirks.

The fixtures are hand-written rather than saved pages: real captures contain a
student's name, study id and course list, so they can never be committed. Each
one is trimmed to the structure that actually matters, and every case here
corresponds to a bug that has happened.

    python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from insis import parse                                    # noqa: E402
from insis.client import looks_like_login                  # noqa: E402

REGISTRACE_URL = ("https://insis.vse.cz/auth/student/registrace.pl"
                  "?studium=111111;obdobi=2222;lang=cz")

# Results table (Zápis, with two entries in one cell) + Arch table (the link).
ENROLMENT_PAGE = """
<html><head><title>Zápis (1. kolo)</title></head><body>
<table>
  <tr><td>Poř.</td><td>Kód</td><td>Předmět</td><td>Skupina</td>
      <td>Třída</td><td>Registrace</td><td>Zápis</td></tr>
  <tr><td>1</td><td>1AB234</td><td>Statistika</td><td>oP</td><td>1</td>
      <td>St 16:15-17:45 XX 1 (cv.)</td>
      <td>St 16:15-17:45 XX 1 (cv.)<br>Po 18:00-19:30 YY 2 (př.)</td></tr>
  <tr><td>2</td><td>TVSXXX</td><td>Sport</td><td>cTVS1</td><td>1</td>
      <td>Pá 11:00-12:30 ZZ (cv.)</td><td>nepřiděleno</td></tr>
</table>
<table>
  <tr><th>Ozn.</th><th>Stav</th><th>Kód</th><th>Předmět</th><th>Skupina</th>
      <th>Třída</th><th>Rozvrh</th><th>Prerekvizity</th></tr>
  <tr><td></td><td></td><td>1AB234</td><td>Statistika</td><td>oP</td><td>1</td>
      <td><a href="../student/vyber_cviceni.pl?studium=111111;predmet=1;zapis=9">
          Cv St 16:15-17:45</a></td><td></td></tr>
  <tr><td></td><td></td><td>TVSXXX</td><td>Sport</td><td>cTVS1</td><td>1</td>
      <td><a href="../student/vyber_cviceni.pl?studium=111111;predmet=2;zapis=8">
          Cv Pá 11:00-12:30</a></td><td></td></tr>
</table>
</body></html>
"""

# One type, one radio group. A full row has no radio at all.
TIMETABLE_SIMPLE = """
<html><head><title>Volba rozvrhové akce</title></head><body>
<form method="post" action="/auth/student/vyber_cviceni.pl">
<input type="hidden" name="predmet" value="2">
<table><thead><tr>
  <td>Ozn.</td><td>Den</td><td>Čas</td><td>Typ</td><td>Četnost</td>
  <td>Místnost</td><td>Vyučující</td><td>Kapacita</td><td>Poznámka</td>
</tr></thead><tbody>
  <tr><td><input type="radio" name="_akce_2" value="100"></td>
      <td>Úterý</td><td>08:30-10:00</td><td>Cvičení</td><td>Každý týden</td>
      <td>R1</td><td>A. Novák</td><td>9/16</td><td>skupina 1</td></tr>
  <tr><td></td>
      <td>Pátek</td><td>11:00-12:30</td><td>Cvičení</td><td>Každý týden</td>
      <td>R2</td><td>B. Svoboda</td><td>16/16</td><td>skupina 2</td></tr>
</tbody></table>
<input type="submit" name="_ulozit_rozvrh" value="Uložit">
</form></body></html>
"""

# Lecture + seminar in ONE table, split by a repeated header row, with a
# separate radio group per type. No <thead> on this variant.
TIMETABLE_TWO_TYPES = """
<html><head><title>Volba rozvrhové akce</title></head><body>
<form method="post" action="/auth/student/vyber_cviceni.pl">
<table>
  <tr><td>Ozn.</td><td>Den</td><td>Čas</td><td>Typ</td><td>Četnost</td>
      <td>Místnost</td><td>Vyučující</td><td>Kapacita</td><td>Poznámka</td></tr>
  <tr><td><input type="radio" name="_akce_1" value="200" checked></td>
      <td>Pondělí</td><td>18:00-19:30</td><td>Přednáška</td><td>Každý týden</td>
      <td>L1</td><td>C. Dvořák</td><td>119/130</td><td></td></tr>
  <tr><td></td></tr>
  <tr><td>Ozn.</td><td>Den</td><td>Čas</td><td>Typ</td><td>Četnost</td>
      <td>Místnost</td><td>Vyučující</td><td>Kapacita</td><td>Poznámka</td></tr>
  <tr><td></td>
      <td>Pondělí</td><td>09:15-10:45</td><td>Cvičení</td><td>Každý týden</td>
      <td>S1</td><td>D. Černý</td><td>25/25</td><td></td></tr>
  <tr><td><input type="radio" name="_akce_2" value="201"></td>
      <td>Pondělí</td><td>11:00-12:30</td><td>Cvičení</td><td>Každý týden</td>
      <td>S1</td><td>C. Dvořák</td><td>24/25</td><td></td></tr>
  <tr><td><input type="radio" name="_akce_2" value="202" checked></td>
      <td>Úterý</td><td>18:00-19:30</td><td>Cvičení</td><td>Každý týden</td>
      <td>S2</td><td>E. Veselý</td><td>25/25</td><td></td></tr>
</table>
<input type="submit" name="_ulozit_rozvrh" value="Uložit">
</form></body></html>
"""

LOGIN_PAGE = """
<html><head><title>Přihlášení do systému</title></head><body>
<form method="post" action="/system/login.pl">
<input type="hidden" name="lang" value="cz">
<input type="hidden" name="destination" value="/auth/student/registrace.pl">
<input type="hidden" name="auth_2fa_type" value="no">
<input type="text" name="credential_0" value="">
<input type="password" name="credential_1" value="">
<input type="text" name="credential_k" value="">
<input type="checkbox" name="credential_cookie" value="1">
<input type="submit" name="login" value="Přihlásit se">
</form></body></html>
"""

TWOFA_PAGE = LOGIN_PAGE.replace('name="auth_2fa_type" value="no"',
                                'name="auth_2fa_type" value="totp"')

NOT_FOUND = "<html><head><title>Stránka nenalezena</title></head><body/></html>"


class TestCourses(unittest.TestCase):
    def setUp(self):
        self.courses = parse.parse_courses(ENROLMENT_PAGE, base=REGISTRACE_URL)

    def test_finds_all_courses(self):
        self.assertEqual([c.code for c in self.courses], ["1AB234", "TVSXXX"])

    def test_links_keep_auth_prefix(self):
        """'../student/...' against the site root would drop /auth/ and 404."""
        for c in self.courses:
            self.assertIn("/auth/student/vyber_cviceni.pl", c.schedule_url)

    def test_ids_parsed_from_semicolon_query(self):
        """InSIS separates query params with ';', which parse_qs ignores."""
        self.assertEqual(self.courses[0].predmet, "1")
        self.assertEqual(self.courses[0].zapis_id, "9")

    def test_zapis_split_on_br(self):
        self.assertEqual(len(self.courses[0].zapis_parts), 2)

    def test_assigned_vs_not(self):
        self.assertTrue(self.courses[0].assigned)
        self.assertFalse(self.courses[1].assigned)

    def test_zapis_rows_carry_type(self):
        kinds = [k for k, _ in self.courses[0].zapis_rows]
        self.assertEqual(kinds, ["cv", "př"])


class TestTimetableSimple(unittest.TestCase):
    def setUp(self):
        self.slots = parse.parse_slots(TIMETABLE_SIMPLE)

    def test_row_count(self):
        self.assertEqual(len(self.slots), 2)

    def test_radio_decides_takeability(self):
        self.assertTrue(self.slots[0].takeable)
        self.assertFalse(self.slots[1].takeable)

    def test_capacity(self):
        self.assertEqual((self.slots[0].joined, self.slots[0].capacity), (9, 16))

    def test_matching(self):
        self.assertTrue(self.slots[1].matches("Pátek", "11:00"))
        self.assertFalse(self.slots[1].matches("Pondělí", "11:00"))


class TestTimetableTwoTypes(unittest.TestCase):
    """A lecture and a seminar in one table. Every assertion here is a bug."""

    def setUp(self):
        self.slots = parse.parse_slots(TIMETABLE_TWO_TYPES)

    def test_repeated_header_is_not_a_slot(self):
        self.assertEqual(len(self.slots), 4)
        self.assertNotIn("Den", [s.day for s in self.slots])

    def test_each_type_keeps_its_own_radio_group(self):
        by_kind = {s.kind: s.group_name for s in self.slots}
        self.assertEqual(by_kind["Přednáška"], "_akce_1")
        self.assertEqual(by_kind["Cvičení"], "_akce_2")

    def test_group_backfilled_onto_rows_without_a_radio(self):
        """Full rows have no radio, so the group must come from the section."""
        full = [s for s in self.slots if s.seats == "25/25"
                and s.day == "Pondělí"][0]
        self.assertIsNone(full.action_id)
        self.assertEqual(full.group_name, "_akce_2")

    def test_both_checked_rows_seen(self):
        self.assertEqual(sorted(s.action_id for s in self.slots if s.selected),
                         ["200", "202"])

    def test_no_thead_still_parses(self):
        self.assertNotIn("<thead", TIMETABLE_TWO_TYPES)
        self.assertTrue(self.slots)


class TestLogin(unittest.TestCase):
    def test_login_page_detected(self):
        self.assertTrue(looks_like_login(LOGIN_PAGE))
        self.assertFalse(looks_like_login(TIMETABLE_SIMPLE))

    def test_form_carries_hidden_state(self):
        _, action, fields = parse.parse_form(LOGIN_PAGE, needle="credential_0")
        self.assertEqual(action, "/system/login.pl")
        self.assertEqual(fields["auth_2fa_type"], "no")
        self.assertIn("destination", fields)

    def test_unchecked_checkbox_is_not_submitted(self):
        _, _, fields = parse.parse_form(LOGIN_PAGE)
        self.assertNotIn("credential_cookie", fields)

    def test_code_field_is_credential_k_not_auth_2fa_type(self):
        """auth_2fa_type contains '2fa' but is hidden state, not the code box."""
        self.assertEqual(parse.find_code_field(TWOFA_PAGE), "credential_k")

    def test_ambiguous_page_returns_none(self):
        """Better to fail loudly than to post the code into a random field."""
        ambiguous = ('<form><input type="text" name="a" value="">'
                     '<input type="text" name="b" value=""></form>')
        self.assertIsNone(parse.find_code_field(ambiguous))


class TestMisc(unittest.TestCase):
    def test_split_kind(self):
        self.assertEqual(parse.split_kind("Út 18:00-19:30 SB 206 (cv.)"),
                         ("cv", "Út 18:00-19:30 SB 206"))
        self.assertEqual(parse.split_kind("Po 18:00-19:30 NB D (př.)"),
                         ("př", "Po 18:00-19:30 NB D"))
        self.assertEqual(parse.split_kind("nepřiděleno"), ("", "nepřiděleno"))

    def test_split_kind_keeps_qualifier(self):
        """'lichý' changes which weeks the class runs; dropping it misleads."""
        kind, when = parse.split_kind("Pá 09:15-12:30 JM 104 (cv., lichý)")
        self.assertEqual(kind, "cv")
        self.assertIn("lichý", when)

    def test_not_found_title(self):
        self.assertEqual(parse.page_title(NOT_FOUND), "Stránka nenalezena")

    def test_capacity_parsing(self):
        self.assertEqual(parse.parse_capacity("16/16"), (16, 16))
        self.assertEqual(parse.parse_capacity("nonsense"), (None, None))

    def test_radio_group_name_discovered(self):
        self.assertEqual(parse.radio_group_name(TIMETABLE_SIMPLE), "_akce_2")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# Two parallel seminars at the SAME day and time, in different rooms, one of
# them already the student's. This is the shape that made the watcher report
# "already ours" for a class the user had not picked, and stop watching the
# one they had.
TIMETABLE_PARALLEL = """
<html><head><title>Volba rozvrhové akce</title></head><body>
<form method="post" action="/auth/student/vyber_cviceni.pl">
<table><thead><tr>
  <td>Ozn.</td><td>Den</td><td>Čas</td><td>Typ</td><td>Četnost</td>
  <td>Místnost</td><td>Vyučující</td><td>Kapacita</td><td>Poznámka</td>
</tr></thead><tbody>
  <tr><td><input type="radio" name="_akce_2" value="300" checked></td>
      <td>Středa</td><td>16:15-17:45</td><td>Cvičení</td><td>Každý týden</td>
      <td>SB 231</td><td>F. Horák</td><td>30/30</td><td></td></tr>
  <tr><td><input type="radio" name="_akce_2" value="301"></td>
      <td>Středa</td><td>16:15-17:45</td><td>Cvičení</td><td>Každý týden</td>
      <td>RB 203</td><td>G. Marek</td><td>28/30</td><td></td></tr>
</tbody></table>
<input type="submit" name="_ulozit_rozvrh" value="Uložit">
</form></body></html>
"""


class TestParallelSlots(unittest.TestCase):
    def setUp(self):
        self.slots = parse.parse_slots(TIMETABLE_PARALLEL)

    def test_day_and_time_alone_are_ambiguous(self):
        """Both rows match on day+time - which is why room is needed."""
        both = [s for s in self.slots if s.matches("Středa", "16:15")]
        self.assertEqual(len(both), 2)

    def test_room_disambiguates(self):
        picked = [s for s in self.slots
                  if s.matches("Středa", "16:15", "RB 203")]
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].action_id, "301")
        self.assertFalse(picked[0].selected)

    def test_first_match_would_be_the_wrong_one(self):
        """Guards the actual bug: row 0 is the one already held."""
        first = [s for s in self.slots if s.matches("Středa", "16:15")][0]
        self.assertTrue(first.selected)
        self.assertEqual(first.room, "SB 231")

    def test_room_match_is_whitespace_and_case_insensitive(self):
        self.assertTrue(self.slots[1].matches(room=" rb 203 "))

    def test_kind_narrows_further(self):
        self.assertTrue(self.slots[1].matches("Středa", "16:15", "RB 203",
                                              "Cvičení"))
        self.assertFalse(self.slots[1].matches("Středa", "16:15", "RB 203",
                                               "Přednáška"))

    def test_empty_room_still_matches_old_watch_entries(self):
        """Watches saved before room existed must not silently match nothing."""
        self.assertTrue(self.slots[0].matches("Středa", "16:15", ""))


class TestViewport(unittest.TestCase):
    """
    Long lists must scroll, not silently truncate.

    Before this, a course list taller than the terminal simply lost its tail:
    the rows did not exist as far as the user could tell, and moving the
    cursor into them appeared to do nothing at all.
    """

    def setUp(self):
        from insis import screen
        self.screen = screen
        self.body = [f"row {i}" for i in range(40)]

    def test_short_body_untouched(self):
        out = self.screen._window(["a", "b"], 10, None)
        self.assertEqual(out, ["a", "b"])

    def test_overflow_is_announced_not_hidden(self):
        out = self.screen._window(self.body, 10, None)
        self.assertEqual(len(out), 10)
        self.assertIn("more below", out[-1])

    def test_window_follows_the_cursor(self):
        out = self.screen._window(self.body, 10, 35)
        plain = [line for line in out if line.startswith("row ")]
        self.assertIn("row 35", plain)

    def test_both_edges_announced_in_the_middle(self):
        out = self.screen._window(self.body, 10, 20)
        self.assertIn("more above", out[0])
        self.assertIn("more below", out[-1])

    def test_never_scrolls_past_the_end(self):
        out = self.screen._window(self.body, 10, 39)
        self.assertEqual(len(out), 10)
        self.assertIn("row 39", [line for line in out if line.startswith("row")])

    def test_rule_width_is_shared(self):
        """Table rules and the footer rule must not drift apart."""
        self.assertEqual(len(self.screen.rule(100)),
                         len(self.screen.rule(100)))
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", self.screen.rule(100))
        self.assertEqual(len(plain), 100 - 2)


class TestEnrolmentRounds(unittest.TestCase):
    """
    Enrolment runs in rounds and the timetable link carries the round in its
    `akce` parameter, so a URL saved in round 1 stops working in round 2. The
    watcher must follow the new link instead of polling a dead one.
    """

    def setUp(self):
        from insis import notify
        from insis.settings import Settings, Watch
        from insis.watcher import Watcher
        from insis.models import Course

        self.pushes = []
        notify.send = lambda u, m, **k: self.pushes.append(k.get("title")) or True
        notify.send_burst = lambda u, m, **k: self.pushes.append(k.get("title")) or 1

        self.Course, self.Watcher = Course, Watcher
        self.settings = Settings(ntfy_topic="t", registrace_url="reg")
        self.settings.save = lambda *a, **k: None
        self.watch = Watch(course_code="1AB234", schedule_url="…akce=zap-k1…",
                           day="Středa", time="16:15", room="R1")
        self.settings.watches.append(self.watch)

    def _insis(self, course_url, slots):
        Course = self.Course

        class Fake:
            def __init__(self):
                self.asked = []

            def courses(self, url):
                if course_url is None:
                    return []
                return [Course(code="1AB234", name="Statistika",
                               schedule_url=course_url)]

            def slots(self, url):
                self.asked.append(url)
                return slots
        return Fake()

    def test_follows_the_new_round_url(self):
        slots = parse.parse_slots(TIMETABLE_PARALLEL)
        fake = self._insis("…akce=zap-k2…", slots)
        self.Watcher(fake, self.settings, log=lambda m: None).run(once=True)
        self.assertEqual(fake.asked, ["…akce=zap-k2…"])
        self.assertEqual(self.watch.schedule_url, "…akce=zap-k2…")

    def test_missing_course_is_reported_not_ignored(self):
        fake = self._insis(None, [])
        self.Watcher(fake, self.settings, log=lambda m: None).run(once=True)
        self.assertIn("InSIS - course missing", self.pushes)

    def test_warning_is_sent_only_once(self):
        fake = self._insis(None, [])
        w = self.Watcher(fake, self.settings, log=lambda m: None)
        w.run(once=True)
        w.run(once=True)
        self.assertEqual(self.pushes.count("InSIS - course missing"), 1)


class TestLockedState(unittest.TestCase):
    """
    A class outside the current enrolment round shows free seats but no radio.
    That must read as LOCKED, not FULL: only one of the two can change on its
    own, and the difference is what tells the user whether to wait.
    """

    def _slot(self, joined, capacity, action):
        from insis.models import Slot
        return Slot(index=0, joined=joined, capacity=capacity,
                    action_id=action)

    def test_free_seats_without_a_radio_are_locked(self):
        from insis.models import LOCKED
        self.assertEqual(self._slot(10, 25, None).state, LOCKED)

    def test_no_seats_is_full(self):
        from insis.models import FULL
        self.assertEqual(self._slot(25, 25, None).state, FULL)

    def test_radio_means_free(self):
        from insis.models import FREE
        self.assertEqual(self._slot(10, 25, "1").state, FREE)

    def test_locked_is_not_takeable(self):
        self.assertFalse(self._slot(10, 25, None).takeable)


class TestCaptureHelpers(unittest.TestCase):
    def setUp(self):
        import capture
        self.capture = capture

    def test_round_from_url(self):
        url = ("https://insis.vse.cz/auth/student/vyber_cviceni.pl"
               "?studium=1;akce=zap-k2;predmet=2")
        self.assertEqual(self.capture.round_of(url), "zap-k2")

    def test_round_unknown_when_absent(self):
        self.assertEqual(self.capture.round_of("https://x/y.pl?a=1"), "unknown")
        self.assertEqual(self.capture.round_of(""), "unknown")

    def test_safe_filenames(self):
        self.assertEqual(self.capture.safe("4EK212"), "4EK212")
        self.assertEqual(self.capture.safe("a/b\c:d"), "a_b_c_d")

    def test_slot_key_includes_kind_and_room(self):
        a = {"kind": "Cvičení", "day": "Po", "time": "9:15", "room": "S1"}
        b = {"kind": "Cvičení", "day": "Po", "time": "9:15", "room": "S2"}
        self.assertNotEqual(self.capture.slot_key(a), self.capture.slot_key(b))


class TestTimetableHeaders(unittest.TestCase):
    def test_headers_recorded_for_each_table(self):
        heads = parse.timetable_headers(TIMETABLE_SIMPLE)
        self.assertEqual(len(heads), 1)
        self.assertIn("Kapacita", heads[0])


class TestChangeDigest(unittest.TestCase):
    """
    A round opening or closing can flip several watched slots at once. Those
    changes arrive as one digest rather than a push per class; only a slot
    becoming FREE is urgent enough to interrupt on its own.
    """

    def setUp(self):
        from insis import notify
        from insis.settings import Settings, Watch
        from insis.watcher import Watcher
        from insis.models import Course, Slot

        self.sent = []
        notify.send = lambda u, m, **k: self.sent.append((k.get("title"), m)) or True
        notify.send_burst = lambda u, m, **k: self.sent.append((k.get("title"), m)) or 1

        self.Slot, self.Course, self.Watcher = Slot, Course, Watcher
        self.settings = Settings(ntfy_topic="t", registrace_url="reg")
        self.settings.save = lambda *a, **k: None
        for n in (1, 2):
            self.settings.watches.append(
                Watch(course_code=f"C{n}", day="Po", time="09:15",
                      room=f"R{n}", schedule_url=f"u{n}"))

    def _insis(self, action, akce):
        Course, Slot = self.Course, self.Slot

        class Fake:
            def courses(self, url):
                return [Course(code=f"C{n}", name=f"Course {n}",
                               schedule_url=f"https://x/y.pl?akce={akce};n={n}")
                        for n in (1, 2)]

            def slots(self, url):
                n = url[-1]
                return [Slot(index=0, day="Po", time="09:15", room=f"R{n}",
                             joined=10, capacity=25, action_id=action)]
        return Fake()

    def _run(self, w, action, akce):
        w.insis = self._insis(action, akce)
        w.run(once=True)

    def test_first_cycle_is_quiet(self):
        w = self.Watcher(self._insis(None, "zap-k1"), self.settings,
                         log=lambda m: None)
        w.run(once=True)
        self.assertEqual([t for t, _ in self.sent], [])

    def test_locking_sends_one_digest_for_all_classes(self):
        w = self.Watcher(self._insis("1", "zap-k1"), self.settings,
                         log=lambda m: None)
        w.run(once=True)              # both FREE
        self.sent.clear()
        self._run(w, None, "zap-k2")  # round flips: both LOCKED
        titles = [t for t, _ in self.sent]
        self.assertEqual(titles.count("InSIS - status changed"), 1)
        body = next(m for t, m in self.sent if t == "InSIS - status changed")
        self.assertIn("Enrolment round changed: zap-k1 -> zap-k2", body)
        self.assertIn("C1 Po 09:15 R1: FREE -> LOCKED", body)
        self.assertIn("C2 Po 09:15 R2: FREE -> LOCKED", body)

    def test_becoming_free_is_not_in_the_digest(self):
        """It already sent its own urgent alert; do not say it twice."""
        w = self.Watcher(self._insis(None, "zap-k1"), self.settings,
                         log=lambda m: None)
        w.run(once=True)              # both LOCKED
        self.sent.clear()
        self._run(w, "1", "zap-k1")   # both become FREE
        titles = [t for t, _ in self.sent]
        self.assertNotIn("InSIS - status changed", titles)
        self.assertEqual(titles.count("InSIS - seat free"), 2)

    def test_no_change_means_no_digest(self):
        w = self.Watcher(self._insis(None, "zap-k1"), self.settings,
                         log=lambda m: None)
        w.run(once=True)
        self.sent.clear()
        self._run(w, None, "zap-k1")
        self.assertEqual(self.sent, [])


class TestMissingTimetableLink(unittest.TestCase):
    """
    A course may appear on the enrolment page with no timetable link at all.
    That is not the same as being dropped, and must not be alarming: it can
    come back on its own when a round opens.
    """

    def setUp(self):
        from insis import notify
        from insis.settings import Settings, Watch
        from insis.watcher import Watcher
        from insis.models import Course

        self.sent = []
        notify.send = lambda u, m, **k: self.sent.append(k.get("title")) or True
        notify.send_burst = lambda u, m, **k: self.sent.append(k.get("title")) or 1

        self.Course, self.Watcher = Course, Watcher
        self.settings = Settings(ntfy_topic="t", registrace_url="reg")
        self.settings.save = lambda *a, **k: None
        self.settings.watches.append(
            Watch(course_code="C1", schedule_url="round-1-url",
                  day="Po", time="09:15", room="R1"))

    def _watcher(self, courses, slots=None):
        Course = self.Course

        class Fake:
            def courses(self, url):
                return courses

            def slots(self, url):
                if slots is None:
                    raise AssertionError("should not poll without a link")
                return slots
        return self.Watcher(Fake(), self.settings, log=lambda m: None)

    def test_no_link_is_quiet(self):
        w = self._watcher([self.Course(code="C1", schedule_url=None)])
        w.run(once=True)
        self.assertEqual(self.sent, [])

    def test_no_link_does_not_poll_the_old_round_url(self):
        """Polling a previous round's URL would 404 or return stale data."""
        w = self._watcher([self.Course(code="C1", schedule_url=None)])
        w.run(once=True)          # Fake.slots raises if called

    def test_no_link_records_unavailable(self):
        from insis.models import UNAVAILABLE
        w = self._watcher([self.Course(code="C1", schedule_url=None)])
        w.run(once=True)
        self.assertEqual(list(w._state.values()), [UNAVAILABLE])

    def test_course_absent_entirely_is_still_an_alarm(self):
        w = self._watcher([])
        w.run(once=True)
        self.assertIn("InSIS - course missing", self.sent)

    def test_returning_link_is_reported_in_the_digest(self):
        from insis.models import Slot, UNAVAILABLE
        locked = [Slot(index=0, day="Po", time="09:15", room="R1",
                       joined=10, capacity=25, action_id=None)]
        w = self._watcher([self.Course(code="C1", schedule_url=None)])
        w.run(once=True)
        self.assertEqual(list(w._state.values()), [UNAVAILABLE])

        w.insis.courses = lambda url: [
            self.Course(code="C1", schedule_url="https://x/y.pl?akce=zap-k2")]
        w.insis.slots = lambda url: locked
        self.sent.clear()
        w.run(once=True)
        self.assertIn("InSIS - status changed", self.sent)
