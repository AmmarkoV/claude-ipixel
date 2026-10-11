"""The GitHub punch card: commits per weekday and hour, across the
repositories listed in github-repos.txt.

The counts come straight out of each repository's local history -- one
full-history `git log` per repository, grouped by weekday and hour of the
author date -- so no GitHub API, no rate limits, and private repositories
count like any other. The scan is cached behind the same mtime trick as
today's stats: a commit rewrites `.git/logs/HEAD`, so its mtime alone says
whether the punch card has moved. An idle set of repositories therefore
costs a `stat` per repository, not a process.

The whole module is optional: with no github-user.txt there is no login,
and nothing here ever touches git.
"""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from repos import fingerprint, read_paths

HOURS = 24
DAYS = 7
DAY_NAMES = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")

# The punch card is the whole history, so a new commit barely moves it.
# Rechecking the repositories every few hours is plenty -- it is not meant
# to run often; between checks an idle set costs a `stat` per repository,
# not a process.
REFRESH_SECONDS = 6 * 3600

# A punch card walks the entire history, so it can take a while on a big
# repository where the midnight scan is instant.
GIT_TIMEOUT = 30.0

logger = logging.getLogger("claude-ipixel")


@dataclass
class PunchCard:
    """Commits per (weekday, hour), Sunday-first -- GitHub's day 0 is Sunday."""

    cells: list[list[int]] = field(
        default_factory=lambda: [[0] * HOURS for _ in range(DAYS)]
    )

    @property
    def total(self) -> int:
        return sum(map(sum, self.cells))

    @property
    def peak(self) -> int:
        return max(map(max, self.cells))


def _add(card: PunchCard, cells: PunchCard) -> None:
    for day in range(DAYS):
        for hour in range(HOURS):
            card.cells[day][hour] += cells.cells[day][hour]


def scan(path: Path) -> PunchCard:
    """Every commit on HEAD, grouped by weekday and hour of its author date.

    `%w` is the Sunday-first day number and `%H` the zero-padded hour, so
    each line is one byte of separator plus three digits -- "014" is Sunday
    at two in the afternoon. Merges count like any other commit.
    """
    card = PunchCard()
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "log", "--format=%x01%ad", "--date=format:%w%H"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git log failed in %s: %s", path, exc)
        return card

    if result.returncode != 0:
        logger.warning("git log failed in %s: %s", path, result.stderr.strip()[:120])
        return card

    for line in result.stdout.splitlines():
        card.cells[int(line[1])][int(line[2:])] += 1
    return card


class Scanner:
    """Serves the punch card, rescanning a repository only when it has moved.

    Unlike today's stats the answer is append-only -- new commits land on
    old cells, none move -- so the cache key is the fingerprint alone. One
    dirty repository is scanned per tick rather than all at once: a first
    scan walks every repository's whole history, and that would hold the
    frame up for as many timeouts as there are repositories. The refresh
    throttle only starts once the card is complete, so that first scan
    drains over the first few ticks instead of the first few refresh
    windows.
    """

    def __init__(self, paths: list[Path], refresh: float = REFRESH_SECONDS):
        self.paths = paths
        self.refresh = refresh
        self._cache: dict[Path, tuple[object, PunchCard]] = {}
        self._card = PunchCard()
        self._checked_at: float | None = None
        self._next = 0  # which repository gets the next scan

    def stats(self, now: float | None = None) -> PunchCard:
        now = time.monotonic() if now is None else now
        if self._checked_at is not None and now - self._checked_at < self.refresh:
            return self._card
        if self._scan_one():
            return self._card  # still draining the first scan: no throttle yet
        # Complete: nothing dirty, so start the clock on the next refresh.
        self._checked_at = now
        return self._card

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
            _add(self._card, scanned)
            self._next = (self._next + offset + 1) % len(self.paths)
            return True
        return False


def summary(card: PunchCard | None) -> str:
    if card is None or not card.total:
        return "punch --"
    day, hour, _ = max(
        (
            (day, hour, card.cells[day][hour])
            for day in range(DAYS)
            for hour in range(HOURS)
        ),
        key=lambda slot: slot[2],
    )
    return f"punch {card.total:,} commits, peak {DAY_NAMES[day]} {hour:02d}:00"


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Commits per weekday and hour as a table.")
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
    card = scanner.stats()
    print("     " + " ".join(f"{hour:3d}" for hour in range(HOURS)))
    for day, name in enumerate(DAY_NAMES):
        print(f"{name}  " + " ".join(f"{count:3d}" for count in card.cells[day]))
    print(f"total {card.total:,} commits")
