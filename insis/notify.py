"""
Push notifications via ntfy.sh.

Same idea as the standalone ping.py, but taking the topic from Settings rather
than a module constant, so the app works for anyone who runs it.

Sending never raises. A watcher that dies because a notification failed is
worse than one that misses a notification, and the caller is usually holding a
slot it has just claimed.
"""

import sys
import time

import requests


def send(url: str, message: str, title: str = "InSIS",
         priority: str = "high", tags: str = "calendar",
         click: str | None = None) -> bool:
    """One push. Returns whether ntfy accepted it."""
    if not url:
        print("  (no ntfy topic configured - not notifying)", file=sys.stderr)
        return False

    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
        "Tags": tags,
    }
    if click:
        headers["Click"] = click
    try:
        r = requests.post(url, data=message.encode("utf-8"),
                          headers=headers, timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  ntfy send failed: {e}", file=sys.stderr)
        return False


def send_burst(url: str, message: str, count: int = 5, every: int = 20,
               title: str = "InSIS", priority: str = "urgent",
               tags: str = "calendar", click: str | None = None) -> int:
    """
    The same alert several times, spaced out, so it survives a locked phone.

    Each push is numbered because otherwise the phone collapses identical
    notifications into one line and the repetition becomes invisible. Blocks
    while it sleeps between pushes.
    """
    ok = 0
    for i in range(1, count + 1):
        body = message if count == 1 else f"{message}\n({i}/{count})"
        if send(url, body, title=title, priority=priority, tags=tags, click=click):
            ok += 1
        if i < count:
            time.sleep(every)
    return ok
