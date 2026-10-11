"""The GitHub contribution graph: commits per day over the last year,
across the repositories listed in github-repos.txt.

Like the punch card, the counts come straight out of each repository's
local history -- one full-history `git log` per repository, this time
grouped by calendar date of the author time. The scan is cached behind
the same mtime trick and rechecked on the same few-hour schedule: the
answer is append-only, so a repository that has not committed since costs
a `stat`, not a process.

The whole module is optional: with no github-user.txt there is no login,
and nothing here ever touches git.
"""

import logging
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from punch import GIT_TIMEOUT, REFRESH_SECONDS
from repos import fingerprint, read_paths

WINDOW_DAYS = 365  # the graph walks the last year, GitHub-style

logger = logging.getLogger("claude-ipixel")


@dataclass
class History:
    """Commits per calendar date, from each repository's whole history."""

    days: dict[date, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.days.values())

    def recent_total(self, today: date | None = None) -> int:
        today = today or date.today()
        cutoff = today - timedelta(days=WINDOW_DAYS - 1)
        return sum(count for day, count in self.days.items() if day >= cutoff)


def _merge(history: History, other: History) -> None:
    for day, count in other.days.items():
        history.days[day] = history.days.get(day, 0) + count


def scan(path: Path) -> History:
    """Every commit on HEAD, counted by the calendar date of its author
    time. The format leaves "YYYY-MM-DD" after the separator byte.
    """
    history = History()
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "log", "--format=%x01%ad", "--date=format:%Y-%m-%d"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git log failed in %s: %s", path, exc)
        return history

    if result.returncode != 0:
        logger.warning("git log failed in %s: %s", path, result.stderr.strip()[:120])
        return history

    for day, count in Counter(line[1:] for line in result.stdout.splitlines()).items():
        history.days[date.fromisoformat(day)] = count
    return history


class Scanner:
    """Serves the history, rescanning a repository only when it has moved.

    The same shape as the punch card's scanner: append-only data behind a
    fingerprint cache, one dirty repository per tick so the first full
    scan never holds the frame up, and the refresh throttle only starts
    once the card is complete.
    """

    def __init__(self, paths: list[Path], refresh: float = REFRESH_SECONDS):
        self.paths = paths
        self.refresh = refresh
        self._cache: dict[Path, tuple[object, History]] = {}
        self._history = History()
        self._checked_at: float | None = None
        self._next = 0  # which repository gets the next scan

    def stats(self, now: float | None = None) -> History:
        now = time.monotonic() if now is None else now
        if self._checked_at is not None and now - self._checked_at < self.refresh:
            return self._history
        if self._scan_one():
            return self._history  # still draining the first scan: no throttle yet
        # Complete: nothing dirty, so start the clock on the next refresh.
        self._checked_at = now
        return self._history

    def warm(self) -> None:
        """Scan every dirty repository now, for one-shot runs -- a preview
        exits long before a tick of its own could come round."""
        for _ in range(len(self.paths)):
            if not self._scan_one():
                return

    def _scan_one(self) -> bool:
        """Rescan the next dirty repository, if there is one."""
        for offset in range(len(self.paths)):
            path = self.paths[(self._next + offset) % len(self.paths)]
            key = fingerprint(path)
            cached = self._cache.get(path)
            if cached is not None and cached[0] == key:
                continue
            scanned = scan(path)
            self._cache[path] = (key, scanned)
            _merge(self._history, scanned)
            self._next = (self._next + offset + 1) % len(self.paths)
            return True
        return False


def summary(history: History | None) -> str:
    if history is None:
        return "year --"
    return f"year {history.recent_total():,} commits in the last year"


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Commits per month over the last year as a table.")
    parser.add_argument(
        "--list",
        nargs="?",
        default=None,
        metavar="PATH",
        help=f"repository list file (default: ~/.config/claude-ipixel/repos.txt)",
    )
    args = parser.parse_args()
    listed = None if args.list is None else Path(args.list)
    scanner = Scanner(read_paths(listed))
    scanner.warm()  # stats() alone scans one repository per tick
    history = scanner.stats()
    today = date.today()
    start = today - timedelta(days=WINDOW_DAYS - 1)
    print("| Month | Commits |")
    print("|---|---|")
    month = date(start.year, start.month, 1)
    while month <= today:
        totals = Counter()
        for day, count in history.days.items():
            if day.year == month.year and day.month == month.month:
                totals[""] += count
        print(f"| {month.strftime('%Y-%m')} | {totals['']:,} |")
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    print(f"| **Total** | **{history.recent_total():,}** |")
