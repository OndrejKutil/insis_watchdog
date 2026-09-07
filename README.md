# insis-picker

A terminal app that watches [VŠE InSIS](https://insis.vse.cz) for a seat to
free up in a full class, and either tells you or takes it for you.

InSIS hands out places in enrolment rounds. If the seminar you want is already
full when your round opens, there is no queue and no waiting list — the only
way in is for somebody else to drop out, and then to be the first to notice.
That is a job for a computer.

```
  InSIS slot picker                                    xnovak00 · ntfy ✓ · 1 watched

  #   KÓD       PŘEDMĚT                          TYP ZÁPIS
  ──────────────────────────────────────────────────────────────────────────────
   1   2AJ212    Angličtina pro ekonomická studia cv   Po 16:15-17:45 SB 113
   2   4IT218    Databáze                         cv   Pá 09:15-12:30 JM 104 (lichý)
 › 3   4EK212    Kvantitativní management         cv   Út 18:00-19:30 SB 206
                                                  př   Po 18:00-19:30 NB D
   4   TVSVOL    Volejbal                             !nepřiděleno

  ! no place assigned yet: TVSVOL
  ──────────────────────────────────────────────────────────────────────────────
  ↑↓ move   [Enter] open course   [w] watch list   [s] start watching
  [n] ntfy topic   [r] refresh   [h] help   [q] quit
```

Pick a course, pick a time, choose **notify me** or **take it automatically**,
and leave it running. When a seat opens your phone buzzes — and if you asked
it to, the seat is already yours.

## Why it has no browser

InSIS is a server-rendered Perl application with ordinary `<form>` markup and
no JavaScript worth running. So this talks to it with `requests` and parses the
HTML directly. That means:

- about 30 MB of memory instead of ~400 MB for a headless browser
- headless by nature, so it runs over SSH with no display
- **it runs on a Raspberry Pi**, which a browser-based version does not:
  Playwright ships no ARM64 Linux Chromium

Two pure-Python dependencies. No compiler, no Chromium, no system packages.

## Requirements

- Python 3.11 or newer
- An InSIS account at VŠE
- Optional: the [ntfy](https://ntfy.sh) app on your phone, for notifications

## Install

With [uv](https://docs.astral.sh/uv/) (recommended — it handles Python too):

```bash
git clone https://github.com/YOURNAME/insis-picker.git
cd insis-picker
uv run main.py
```

With plain pip:

```bash
git clone https://github.com/YOURNAME/insis-picker.git
cd insis-picker
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## First run

1. It asks for your InSIS **username**, **password**, and then the **6-digit
   code** from your authenticator app.
2. It finds your enrolment page automatically. If it cannot — for example if
   you have more than one study — it asks you to pick, or to paste the URL from
   *Moje studium → Portál studenta → Registrace/Zápis předmětů*.
3. Press `n` to set an ntfy topic so notifications can reach your phone.

Settings are remembered in `insis_state.json`. **Your password is not**, ever —
see [Credentials](#credentials).

## Using it

Everything is a single keypress; there is no Enter to hunt for.

| Key | Does |
|---|---|
| `↑` `↓` (or `k` `j`) | move the cursor |
| `Enter` | open a course, or watch the selected slot |
| `b` / `←` | back |
| `w` | watch list |
| `d` | remove the selected watch |
| `s` | start watching |
| `n` | set the ntfy topic |
| `r` | reload from InSIS |
| `h` | help, from anywhere |
| `q` | quit |

Digits jump the cursor rather than selecting, so a mistyped `1` on a ten-row
list is a visible move you can correct, not a silent mis-selection.

In a timetable:

- **`FREE`** — a seat is free and InSIS will let you take it
- **`FULL`** — every seat is taken
- **`LOCKED`** — seats are free, but InSIS offers no way to select them;
  normally a class whose enrolment round has not opened yet
- **`YOURS`** — already yours

`FULL` and `LOCKED` both mean "not now", but only `LOCKED` can change on its
own when a round opens — which is why they are shown differently.

A course with both a lecture and a seminar shows them as separate blocks,
because InSIS treats them as separate choices with separate radio groups. The
`TYP` column keeps InSIS's own labels: `cv` for a seminar (cvičení), `př` for a
lecture (přednáška). Course names, rooms, days and notes are shown exactly as
InSIS writes them; everything else is in English.

### Command line

```bash
python main.py                  # the app
python main.py --list           # print your courses and exit
python main.py --watch          # skip the UI, watch the saved list
python main.py --watch --once   # single check, useful for testing
python main.py --every 30       # poll every 30s instead of 60
python main.py --no-fullscreen  # plain scrolling output, for pipes and logs
```

## On a Raspberry Pi

The point of the design. Clone, install, run:

```bash
sudo apt install python3-venv git
git clone https://github.com/YOURNAME/insis-picker.git
cd insis-picker
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Set up your watch list interactively once, then leave it watching.

**Use `tmux` (or `screen`), not systemd.** Logging in needs you to type a
password and a 6-digit code, so it cannot start unattended:

```bash
sudo apt install tmux
tmux new -s insis
.venv/bin/python main.py --watch
# log in, then press Ctrl-B then D to detach
```

Reattach any time with `tmux attach -t insis`. The Pi keeps polling while your
laptop is closed.

If the Pi reboots, or InSIS expires the session, you have to log in again —
that is the cost of never storing your password. It will tell you (see below)
rather than quietly stop.

## Enrolment rounds

Enrolment runs in several rounds, one class at a time. A course outside the
current round shows an **empty ENROLLED column** and a **LOCKED** timetable —
free seats you cannot select yet. That is expected, not a fault.

**Add the watch anyway.** When your round opens the slot becomes `FREE` and the
watcher acts on it immediately, which is exactly when it is most useful.

There is one thing to know about how this is handled. The timetable link
carries the round in it (`akce=zap-k1`), so a link saved in one round stops
working in the next. The watcher therefore **re-reads the enrolment page every
cycle and re-resolves each watched course by its code**, following whatever
link the current round uses. It never relies on the URL it saved.

Three situations are kept distinct, because they need different responses:

| On the enrolment page | Meaning | What you get |
|---|---|---|
| linked | normal | polled as usual |
| listed, but no timetable link | nothing to read; typical of a course whose round is not open | a quiet line in the digest, no alarm |
| absent entirely | the course looks dropped | an urgent push |

If **no** courses can be read from the page at all, that is treated as the page
being unreadable rather than as every course being dropped — a layout change is
far likelier than a mass unenrolment. You get one push about the page, not one
per watched course, and the last known links keep being polled.

The middle case matters: it can come back on its own, so treating it as an
error would mean an alarm at every round boundary. The old round's URL is not
polled as a fallback either, since it would 404 or return stale data.

### What you are told, and when

A round opening or closing can flip several watched classes at once, so those
changes arrive as **one digest**, not a push per class:

```
  InSIS - status changed
  Enrolment round changed: zap-k1 -> zap-k2
  4EK212 Středa 16:15-17:45 RB 203: FREE -> LOCKED
  1FU201 Pondělí 09:15-10:45 SB 231: LOCKED -> FULL
```

A slot becoming **FREE is the exception** — that is time-critical, so it keeps
its own urgent alert (or is claimed outright) and is deliberately left out of
the digest to avoid telling you the same thing twice. The first poll after
startup never sends a digest, since there is nothing to compare against.

## Notifications

Notifications go through [ntfy.sh](https://ntfy.sh) — no account, no API key.
Install the app, subscribe to a topic name, and set the same name with `n`.

You get a push when:

| | |
|---|---|
| **you have the slot** | claimed and verified — the watcher then stops |
| **seat free** | a seat opened (notify-only mode) |
| **enrolment failed** | a seat opened, the claim failed, still watching |
| **not confirmed** | the claim went through but could not be verified |
| **watcher stopped** | the session expired and it cannot log back in |
| **course missing** | a watched course is no longer on your enrolment page |
| **check failing** | a watched slot cannot be read at all |
| **status changed** | one digest when a round opens or closes — see below |

> **The topic name is the only thing protecting your notifications.** Anyone
> who knows it can read them and post to it. Pick something unguessable —
> `insis-a7f3c1e9b2` rather than `insis` — and do not put it in a public repo.

## Auto-take

If you choose **take it automatically**, the app selects the slot and submits
InSIS's own form — the same POST your browser would send. It then **verifies
the result by re-reading the page**, rather than assuming the submission
worked, and tells you which way it went.

It only ever touches the course and the timetable group you picked. It cannot
remove you from anything: the form it submits has no field that could.

Use notify-only if you would rather decide yourself. That is the default.

## Credentials

Your password and 2FA code are **never written to disk**, never logged, and
never sent anywhere except InSIS's own login form over HTTPS. They exist only
in memory for the life of the process.

The trade-off is deliberate: **the watcher cannot log itself back in.** If
InSIS expires your session at three in the morning, it sends an urgent
notification and stops, rather than pretending to still be watching. A silent
failure would be worse than an honest one.

`insis_state.json` holds only your username, your enrolment page URL, your ntfy
topic and your watch list. It is gitignored, as is `captures/`, which can
contain saved pages with your name and study id.

## How it works

| File | What it does |
|---|---|
| `main.py` | Entry point |
| `insis/cli.py` | The full-screen app: views, cursor, key handling |
| `insis/screen.py` | Alternate-screen rendering, ANSI, layout |
| `insis/keys.py` | Single-keypress input (msvcrt / termios / line fallback) |
| `insis/client.py` | HTTP session, two-step login, page fetches, `take()` |
| `insis/parse.py` | HTML → dataclasses; every InSIS quirk lives here |
| `insis/models.py` | `Course` and `Slot` |
| `insis/watcher.py` | The polling loop |
| `insis/notify.py` | ntfy pushes |
| `insis/settings.py` | `insis_state.json` |

Tables are found by their header cells and column indexes are read from that
header, never hardcoded — InSIS column order is not contractual and there is no
reason to expect it to survive a semester.

## Capturing pages

InSIS pages differ between enrolment rounds, and the only way to support a
round we have not seen is to capture it next to one we have.

```bash
uv run capture.py                    # snapshot the current round
uv run capture.py --list             # snapshots taken so far
uv run capture.py --compare A B      # what changed between two of them
```

Each run writes a new timestamped directory under `captures/`, so running it
again in a later round never overwrites the earlier one:

```
captures/round-zap-k1-20260907-174500/
  manifest.json     structured summary, used by --compare
  registrace.html   the enrolment page
  TVSVOL.html       one file per course timetable
```

`--compare` reports what actually matters between rounds: the `akce` value,
courses appearing or disappearing, column headers changing, radio group names,
seat counts, and every slot that changed state:

```
  ROUND CHANGED: zap-k1 -> zap-k2

  1AB234 — Statistika
    ENROLLED: - -> ['St 16:15-17:45 XX 1 (cv.)']
    Cvičení|Pondělí|11:00-12:30|S1: LOCKED -> FREE   seats 18/25 -> 24/25
```

These files contain your name, study id and course list. `captures/` is
gitignored — keep it that way, and read any file before sharing it.

## Tests

```bash
python -m unittest discover tests
```

The fixtures are hand-written markup, not saved pages — real captures contain a
student's name and study id and can never be committed. Each test corresponds
to a bug that actually happened: the dropped `/auth/` prefix, the repeated
header row parsed as a phantom slot, the two radio groups on a lecture+seminar
course, and the hidden `auth_2fa_type` field being mistaken for the 2FA code
box.

## Limitations

- **VŠE InSIS only.** Other UIS/InSIS installations are similar but unverified.
- Polls once a minute by default, so you can be up to a minute behind someone
  refreshing the page by hand.
- The machine must stay awake and online.
- Tested against the first enrolment round of ZS 2026/2027. Later rounds are
  handled by re-resolving links rather than by assuming a URL format, but have
  not been observed directly. InSIS markup can change;
  when a page fails to parse the app saves it to `captures/debug/` so the
  cause can be found rather than guessed at.

## Contributing

If a page fails to parse, the saved file in `captures/debug/` is exactly what
is needed to fix it — **but check it before attaching it to an issue**, as it
contains your name and study id.

## Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with or endorsed by VŠE. Use it on your own account.
