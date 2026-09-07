"""Data the parsers produce and everything else consumes."""

from dataclasses import dataclass, field

# Slot states. FULL and LOCKED both mean "you cannot take this now", but for
# different reasons, and only one of them can change on its own:
#   FULL    every seat is taken; a seat has to free up
#   LOCKED  seats are free but InSIS offers no way to select them, which is
#           what a class outside the current enrolment round looks like
YOURS, FREE, LOCKED, FULL = "YOURS", "FREE", "LOCKED", "FULL"

# Not a slot state but a watch state: the course is still on the enrolment
# page, yet offers no timetable link at all, so there is nothing to read.
# Expected between enrolment rounds, so it must not be treated as an error.
UNAVAILABLE = "UNAVAILABLE"


@dataclass
class Slot:
    """One timetable row for a course."""

    index: int
    day: str = ""
    time: str = ""
    kind: str = ""
    frequency: str = ""
    room: str = ""
    teacher: str = ""
    joined: int | None = None
    capacity: int | None = None
    note: str = ""
    # The radio's value: InSIS's id for this timetable action. None means the
    # row had no radio, which is InSIS saying you may not take it.
    action_id: str | None = None
    # Which radio group the row belongs to. A course with a lecture and a
    # seminar has one group per type, and writing an action id into the wrong
    # group changes the wrong thing.
    group_name: str = ""
    selected: bool = False

    @property
    def takeable(self) -> bool:
        """Can this be claimed right now?"""
        return self.action_id is not None and not self.selected

    @property
    def free(self) -> int | None:
        if self.joined is None or self.capacity is None:
            return None
        return self.capacity - self.joined

    @property
    def state(self) -> str:
        if self.selected:
            return YOURS
        if self.takeable:
            return FREE
        # No radio. Free seats mean it is closed to you rather than full,
        # typically because the round for this class has not opened yet.
        return LOCKED if (self.free or 0) > 0 else FULL

    @property
    def seats(self) -> str:
        if self.joined is None or self.capacity is None:
            return "?"
        return f"{self.joined}/{self.capacity}"

    def matches(self, day: str = "", time: str = "", room: str = "",
                kind: str = "") -> bool:
        """
        Does this row identify the same class the user picked?

        Day and time alone are not unique: a course can run two parallel
        seminars in the same slot in different rooms, and matching on day and
        time picks whichever comes first in the table. Room and kind are part
        of the identity whenever the caller knows them, and optional so that
        watches saved before they existed still match on day and time.
        """
        if day and day.lower() not in self.day.lower():
            return False
        if time and time not in self.time:
            return False
        if room and room.strip().lower() != (self.room or "").strip().lower():
            return False
        if kind and kind.strip().lower() != (self.kind or "").strip().lower():
            return False
        return True

    def label(self) -> str:
        return f"{self.day} {self.time} {self.room}".strip()


@dataclass
class Course:
    """A course on the enrolment page, with a link to its timetable."""

    code: str = ""
    name: str = ""
    group: str = ""
    klass: str = ""
    # Absolute URL of the timetable chooser, if the course has one. Some
    # courses offer no choice and have no link. The URL carries the enrolment
    # round, so it is only valid for the round it was read in.
    schedule_url: str | None = None
    schedule_text: str = ""
    # The assigned time(s) from the results table, or ['nepřiděleno'].
    # A list because one course can have both a seminar and a lecture.
    zapis_parts: list[str] = field(default_factory=list)
    predmet: str = ""
    zapis_id: str = ""
    slots: list[Slot] = field(default_factory=list)

    @property
    def zapis(self) -> str:
        return " / ".join(self.zapis_parts)

    @property
    def zapis_rows(self) -> list[tuple[str, str]]:
        """[('cv', 'Út 18:00-19:30 SB 206'), ('př', 'Po 18:00-19:30 NB D')]"""
        from .parse import split_kind
        return [split_kind(x) for x in self.zapis_parts] or [("", "—")]

    @property
    def zapis_short(self) -> str:
        """First entry, with a count of what did not fit."""
        if not self.zapis_parts:
            return "—"
        if len(self.zapis_parts) == 1:
            return self.zapis_parts[0]
        return f"{self.zapis_parts[0]}  +{len(self.zapis_parts) - 1}"

    @property
    def choosable(self) -> bool:
        return self.schedule_url is not None

    @property
    def assigned(self) -> bool:
        """
        Was a place actually assigned, as opposed to merely registered?

        An empty Zápis is not necessarily a failure: during an enrolment round
        that does not cover this course's class, nothing has been decided yet.
        """
        z = self.zapis.strip().lower()
        return bool(z) and "nepřiděleno" not in z and "nepridel" not in z

    def key(self) -> str:
        """Stable identifier for the state file."""
        return self.code or f"{self.predmet}:{self.zapis_id}"
