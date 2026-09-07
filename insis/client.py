"""
Talking to InSIS over plain HTTP.

No browser, on purpose. InSIS is server-rendered Perl with ordinary <form>
markup and no JavaScript worth running, so requests plus an HTML parser is
enough: about 30 MB instead of ~400 MB, headless by nature, and with no
Chromium build needed for the host. That last point is what makes a Raspberry
Pi viable, since Playwright ships no ARM64 Linux Chromium.
"""

import re
from urllib.parse import urljoin

import requests

from . import parse
from .settings import REGISTRACE_NEEDLE

BASE = "https://insis.vse.cz/"
LOGIN_ACTION = "/system/login.pl"

# Seen on the login page's <title>. Used to tell "not logged in" from a real
# page, because InSIS answers with 403 and a login body rather than a redirect.
LOGIN_TITLE = "Přihlášení do systému"
# InSIS 404s with a 200 and this title.
NOT_FOUND_TITLE = "Stránka nenalezena"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class InsisError(Exception):
    """Anything that went wrong talking to InSIS."""


class LoginFailed(InsisError):
    """Credentials or the 2FA code were rejected."""


class SessionExpired(InsisError):
    """We were logged in and no longer are."""


def looks_like_login(html: str) -> bool:
    if LOGIN_TITLE in parse.page_title(html):
        return True
    return 'name="credential_0"' in html and 'action="/system/login.pl"' in html


class Insis:
    def __init__(self, base: str = BASE, timeout: int = 30):
        self.base = base
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            # InSIS caches nothing important, but a 304 on the timetable would
            # freeze a watcher on a stale capacity reading forever.
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        self.logged_in = False

    # -- low level ---------------------------------------------------------

    def _get(self, url: str) -> requests.Response:
        return self.session.get(urljoin(self.base, url), timeout=self.timeout)

    def _post(self, action: str, data: dict) -> requests.Response:
        return self.session.post(
            urljoin(self.base, action), data=data, timeout=self.timeout)

    def get_html(self, url: str, allow_login_page: bool = False) -> str:
        """Fetch a page, refusing to hand back a login screen as if it were data."""
        r = self._get(url)
        if looks_like_login(r.text) and not allow_login_page:
            self.logged_in = False
            raise SessionExpired(
                "InSIS returned the login page - the session is gone")
        # InSIS answers a bad URL with a normal-looking 200 page, which then
        # parses to zero rows and reads as "nothing to show" rather than
        # "you asked for the wrong address". Say which it is.
        if NOT_FOUND_TITLE in parse.page_title(r.text):
            raise InsisError(f"InSIS says 'Stránka nenalezena' for {r.url}")
        return r.text

    # -- login -------------------------------------------------------------

    def login(self, username: str, password: str, get_code=None,
              destination: str | None = None) -> None:
        """
        Log in, handling the second-step 6-digit code.

        `get_code` is called only if InSIS asks for one, so accounts without
        2FA never see a prompt. It should return the code as a string.

        The whole login form is round-tripped rather than posted field by
        field: InSIS threads state through hidden inputs (destination,
        auth_id_hidden, auth_2fa_type) and dropping any of them changes what
        the POST means.
        """
        target = destination or "/auth/student/moje_studium.pl"
        r = self._get(target)
        html = r.text
        if not looks_like_login(html):
            self.logged_in = True
            return

        method, action, fields = parse.parse_form(html, needle="credential_0")
        fields["credential_0"] = username
        fields["credential_1"] = password
        # The submit button's own name/value is part of a normal browser POST
        # and some InSIS handlers branch on it.
        fields.setdefault("login", "Přihlásit se")

        r = self._post(action or LOGIN_ACTION, fields)
        html = r.text

        # Still on a login form? Either the password was wrong, or this is the
        # 2FA step. Presence of an empty code-ish field distinguishes them.
        if looks_like_login(html) or self._wants_code(html):
            html = self._handle_second_step(html, get_code, username, password)

        if looks_like_login(html):
            raise LoginFailed("InSIS still shows the login page - "
                              "wrong username or password?")

        # Prove it rather than trust the redirect we happened to land on.
        check = self._get(target)
        if looks_like_login(check.text):
            raise LoginFailed("login appeared to work but the session is not valid")

        self.logged_in = True

    def _wants_code(self, html: str) -> bool:
        low = html.lower()
        if "ověřovací kód" in low or "verification code" in low:
            return True
        return bool(re.search(r'name="[^"]*(2fa|otp|totp)[^"]*"', low))

    def _handle_second_step(self, html: str, get_code,
                            username: str, password: str) -> str:
        """
        Fill in and submit the 6-digit code page.

        The 2FA step is not a separate form but the same login form re-posted,
        with auth_2fa_type flipped from 'no' to 'totp' and a visible
        credential_k box added. The username comes back empty and the password
        is not echoed, so both must be supplied again alongside the code:
        posting the parsed page back unchanged would submit an empty username.
        """
        try:
            method, action, fields = parse.parse_form(html)
        except ValueError:
            raise LoginFailed("expected a second login step but found no form")

        fields["credential_0"] = username
        fields["credential_1"] = password

        field = parse.find_code_field(html)
        if field is None:
            # Fail loudly with the field names, rather than guessing and
            # posting the code into some arbitrary input.
            raise LoginFailed(
                "could not find the 2FA code field. Fields on the page were: "
                + ", ".join(sorted(fields))
                + " -- report these so the client can be taught this form."
            )

        if get_code is None:
            raise LoginFailed("InSIS wants a 2FA code but no prompt was given")

        code = str(get_code()).strip()
        if not code:
            raise LoginFailed("no 2FA code entered")

        fields[field] = code
        fields.setdefault("login", "Přihlásit se")
        return self._post(action or LOGIN_ACTION, fields).text

    # -- pages -------------------------------------------------------------

    def courses(self, registrace_url: str):
        """
        Every enrolled course, with Zápis status and timetable links.

        The base for resolving links is the *page's own URL*, not the site
        root. The hrefs are relative ('../student/vyber_cviceni.pl'), so
        resolving them against 'https://insis.vse.cz/' makes '..' climb one
        level too far and silently drops '/auth/' -- every link then 404s.
        """
        return parse.parse_courses(
            self.get_html(registrace_url), base=registrace_url)

    def find_registrace_urls(self, moje_studium: str) -> list[tuple[str, str]]:
        """
        Best-effort discovery of the enrollment page for this student.

        The URL embeds studium and obdobi ids unique to the person and the
        semester, so it cannot ship as a constant. 'Moje studium' links to it;
        if the link is not found (or there are several studies) the caller
        falls back to asking the user to paste the URL.
        """
        html = self.get_html(moje_studium)
        return parse.find_links(html, moje_studium, REGISTRACE_NEEDLE)

    def slots(self, schedule_url: str):
        """The timetable rows for one course."""
        return parse.parse_slots(self.get_html(schedule_url))

    def take(self, schedule_url: str, action_id: str) -> str:
        """
        Claim one timetable action. Returns the resulting page's HTML.

        Re-fetches the form immediately before posting so the hidden state is
        current, and refuses to post if the requested action is no longer
        offered -- between spotting a free seat and claiming it, somebody else
        can take it, and InSIS would then be asked for something impossible.
        """
        html = self.get_html(schedule_url)

        # Find the row we mean, and take its radio group from the page as it
        # is right now. A course with a lecture and a seminar has one group
        # per type, so writing into the first group found would change the
        # lecture when the user asked for a seminar.
        wanted = next((s for s in parse.parse_slots(html)
                       if s.action_id == action_id), None)
        if wanted is None:
            raise InsisError(f"action {action_id} is no longer offered")

        group = wanted.group_name or parse.radio_group_name(html)
        if not group:
            raise InsisError("no radio group on the timetable form")

        # parse_form carries every *checked* radio forward, so the other
        # group's existing choice is preserved rather than cleared.
        method, action, fields = parse.parse_form(html, needle="_ulozit_rozvrh")
        fields[group] = action_id
        fields["_ulozit_rozvrh"] = "Uložit"
        return self._post(action or schedule_url, fields).text
