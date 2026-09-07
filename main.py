"""
Entry point for the InSIS slot picker.

    uv run main.py                  # interactive: courses -> times -> watch
    uv run main.py --list           # just print your courses
    uv run main.py --watch          # headless: log in, watch the saved list
    uv run main.py --watch --once   # single pass, for testing

Headless-friendly: no browser, so it runs over SSH on a Raspberry Pi. It does
prompt for username, password and the 6-digit code at startup, because nothing
is stored on disk -- which also means it cannot log itself back in if InSIS
expires the session. It pushes an urgent notification instead of dying quietly.
"""

import sys

from insis.cli import main

if __name__ == "__main__":
    sys.exit(main())
