"""
Where the app keeps what it must remember between runs.

Deliberately not credentials. Those are prompted for on every start and only
ever live in memory -- which is why an expired session cannot be repaired
unattended, and why the watcher shouts instead of dying quietly when that
happens.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "insis_state.json"

# The enrollment page URL carries the student's own studium and obdobi ids, so
# there is no default that could work for anyone else. It is discovered after
# the first login (or pasted in) and remembered in the state file.
MOJE_STUDIUM = "https://insis.vse.cz/auth/student/moje_studium.pl"
REGISTRACE_NEEDLE = "registrace.pl"


@dataclass
class Watch:
    """One slot the daemon should chase."""

    course_code: str
    course_name: str = ""
    schedule_url: str = ""
    day: str = ""
    time: str = ""
    take: bool = False          # claim it, or only notify?
    # Room and kind are part of the slot's identity, not decoration: a course
    # can run two parallel seminars at the same day and time in different
    # rooms. Defaults keep state files written before this readable.
    room: str = ""
    kind: str = ""

    def describe(self) -> str:
        where = f" {self.room}" if self.room else ""
        what = f"{self.course_code} {self.day} {self.time}{where}".strip()
        return f"{what} [{'auto-take' if self.take else 'notify only'}]"


@dataclass
class Settings:
    registrace_url: str = ""
    ntfy_topic: str = ""
    poll_seconds: int = 60
    username: str = ""          # convenience only; never the password
    watches: list[Watch] = field(default_factory=list)

    @property
    def ntfy_url(self) -> str:
        return f"https://ntfy.sh/{self.ntfy_topic}" if self.ntfy_topic else ""

    # -- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "Settings":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        watches = [Watch(**w) for w in raw.pop("watches", []) if isinstance(w, dict)]
        known = {f for f in cls.__dataclass_fields__ if f != "watches"}
        return cls(watches=watches, **{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path = STATE_FILE) -> None:
        data = asdict(self)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- watch list --------------------------------------------------------

    def find_watch(self, code: str, day: str, time: str,
                   room: str = "") -> Watch | None:
        for w in self.watches:
            if (w.course_code == code and w.day == day and w.time == time
                    and w.room == room):
                return w
        return None

    def add_watch(self, w: Watch) -> bool:
        """False if an identical watch was already there."""
        if self.find_watch(w.course_code, w.day, w.time, w.room):
            return False
        self.watches.append(w)
        return True

    def remove_watch(self, w: Watch) -> None:
        self.watches = [x for x in self.watches if x is not w]
