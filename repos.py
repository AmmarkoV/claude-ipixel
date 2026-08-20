"""Today's git activity across the repositories listed in github-repos.txt.

One `git log` per repository per refresh, and even that is skipped while the
repository has not moved: a commit rewrites `.git/logs/HEAD`, so its mtime plus
the local date is enough of a fingerprint to serve the previous answer from
cache. An idle set of repositories therefore costs a handful of `stat` calls,
not a process per poll.
"""

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Where the repository list lives. The list is machine-specific -- absolute
# paths that mean nothing on another host -- so a checkout must not have to
# carry one. The real list belongs in the user's config directory, which
# survives moving, reinstalling or repointing the checkout; the copy beside the
# code is kept only as a fallback for running straight out of a clone.
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "claude-ipixel"
CONFIG_PATH = CONFIG_DIR / "repos.txt"
LOCAL_PATH = Path(__file__).with_name("github-repos.txt")
EXAMPLE_PATH = Path(__file__).with_name("github-repos.txt.example")

REFRESH_SECONDS = 300
GIT_TIMEOUT = 10.0

logger = logging.getLogger("claude-ipixel")


@dataclass
class RepoStats:
    """One repository's committed work since local midnight."""

    name: str
    label: str  # 2-3 characters, all the panel has room for
    commits: int = 0
    added: int = 0
    removed: int = 0

    @property
    def churn(self) -> int:
        return self.added + self.removed

    @property
    def net(self) -> int:
        return self.added - self.removed


@dataclass
class DayStats:
    repos: list[RepoStats] = field(default_factory=list)

    @property
    def commits(self) -> int:
        return sum(repo.commits for repo in self.repos)

    @property
    def added(self) -> int:
        return sum(repo.added for repo in self.repos)

    @property
    def removed(self) -> int:
        return sum(repo.removed for repo in self.repos)

    @property
    def churn(self) -> int:
        return self.added + self.removed

    @property
    def net(self) -> int:
        return self.added - self.removed


def label_for(name: str) -> str:
    """Initials of a multi-word name, else its first three letters."""
    words = [word for word in re.split(r"[^0-9A-Za-z]+", name) if word]
    if not words:
        return "?"
    if len(words) > 1:
        return "".join(word[0] for word in words[:3]).upper()
    return words[0][:3].upper()


def default_path() -> Path:
    """The first repository list that exists, else where one should be made."""
    for candidate in (CONFIG_PATH, LOCAL_PATH):
        if candidate.exists():
            return candidate
    return CONFIG_PATH


def read_paths(path: Path | None = None) -> list[Path]:
    """One repository path per line; blank lines and # comments are ignored."""
    path = default_path() if path is None else path
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        logger.warning("no repository list at %s (%s)", path, exc.strerror or exc)
        logger.warning("create one: mkdir -p %s && cp %s %s", CONFIG_DIR, EXAMPLE_PATH, CONFIG_PATH)
        return []

    paths = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        repo = Path(line).expanduser()
        if not (repo / ".git").exists():
            logger.warning("not a git repository, skipping: %s", repo)
            continue
        paths.append(repo)
    return paths


def _fingerprint(path: Path):
    """Cheap stand-in for "has this repository committed anything since?"."""
    for candidate in (path / ".git" / "logs" / "HEAD", path / ".git"):
        try:
            return candidate.stat().st_mtime_ns
        except OSError:
            continue
    return None


def _scan(path: Path) -> RepoStats:
    """Committed lines and commit count since local midnight, on HEAD.

    Merges are excluded: git prints no numstat for them anyway, so counting
    them would inflate the commit total with work already counted once.
    """
    stats = RepoStats(name=path.name, label=label_for(path.name))
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "log", "--since=midnight", "--no-merges",
             "--numstat", "--format=%x01"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git log failed in %s: %s", path, exc)
        return stats

    if result.returncode != 0:
        # An unborn branch is normal enough; anything else is worth a line.
        logger.warning("git log failed in %s: %s", path, result.stderr.strip()[:120])
        return stats

    for line in result.stdout.splitlines():
        if line.startswith("\x01"):
            stats.commits += 1
            continue
        columns = line.split("\t")
        if len(columns) < 3:
            continue
        added, removed = columns[0], columns[1]
        if added.isdigit():  # binary files report "-"
            stats.added += int(added)
        if removed.isdigit():
            stats.removed += int(removed)
    return stats


class Scanner:
    """Serves today's stats, rescanning a repository only when it has moved."""

    def __init__(self, paths: list[Path], refresh: float = REFRESH_SECONDS):
        self.paths = paths
        self.refresh = refresh
        self._cache: dict[Path, tuple[tuple, RepoStats]] = {}
        self._stats = DayStats()
        self._checked_at: float | None = None

    def stats(self, now: float | None = None) -> DayStats:
        now = time.monotonic() if now is None else now
        if self._checked_at is not None and now - self._checked_at < self.refresh:
            return self._stats

        self._checked_at = now
        today = date.today().toordinal()  # "since midnight" moves at midnight
        repos = []
        for path in self.paths:
            key = (today, _fingerprint(path))
            cached = self._cache.get(path)
            if cached is not None and cached[0] == key:
                repos.append(cached[1])
                continue
            scanned = _scan(path)
            self._cache[path] = (key, scanned)
            repos.append(scanned)
        self._stats = DayStats(repos)
        return self._stats


def summary(stats: DayStats) -> str:
    return f"today {stats.commits} commits +{stats.added}/-{stats.removed}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    today = Scanner(read_paths()).stats()
    for repo in sorted(today.repos, key=lambda repo: repo.churn, reverse=True):
        print(f"{repo.label:>3}  {repo.commits:3d}c  +{repo.added:<6d} -{repo.removed:<6d}  {repo.name}")
    print(f"{'ALL':>3}  {today.commits:3d}c  +{today.added:<6d} -{today.removed:<6d}  net {today.net:+d}")
