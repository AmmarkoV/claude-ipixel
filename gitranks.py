"""Yesterday's standing on gitranks.com for the login in github-user.txt.

The site is behind a Cloudflare managed challenge, so no plain HTTP client can
reach it; the page is rendered once a day in a headless Firefox driven through
geckodriver, and everything after that is served from a JSON file on disk. The
browser keeps its own profile between runs so the clearance cookie survives,
which is what makes one page load a day enough.

The whole module is optional: with no github-user.txt there is no login, and
nothing here ever starts a browser.
"""

import json
import logging
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

from daily import CACHE_DIR, REFRESH_SECONDS, Daily
from repos import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "github-user.txt"
LOCAL_PATH = Path(__file__).with_name("github-user.txt")
EXAMPLE_PATH = Path(__file__).with_name("github-user.txt.example")

PROFILE_URL = "https://gitranks.com/profile/{login}/ranks"

# Firefox, then the page, then the challenge: none of it is quick.
DRIVER_PORT = 4445
DRIVER_TIMEOUT = 120.0
PAGE_TIMEOUT = 90.0
READY_TEXT = "Rank breakdown"  # last thing on the page, so its arrival means "done"

logger = logging.getLogger("claude-ipixel")

# The three cards the profile page is built from, in the order it draws them,
# keyed by the one-letter names gitranks uses for them internally.
CARDS = {"s": "Stars Rank", "c": "Contributor Rank", "f": "Followers Rank"}


@dataclass
class Rank:
    """One of the three rankings, as the profile page states it."""

    kind: str  # "s", "c" or "f"
    tier: str = ""  # "Master 5"
    position: int = 0  # 47939
    ranked: int = 0  # 1600000 profiles ranked at all
    top_percent: int = 0  # 3, as in "top 3%"
    month_change: int = 0  # +1940 places gained this month
    score: int = 0  # 843 stars, or followers for the follower rank
    next_tier: str = ""  # "Elite 1"
    to_next: int = 0  # 2 more stars gets there

    @property
    def label(self) -> str:
        return self.kind.upper()

    @property
    def percentile(self) -> float:
        """Share of ranked profiles below this one, 0-1. The bar to draw."""
        if not self.ranked or not self.position:
            return 0.0
        return max(0.0, 1.0 - self.position / self.ranked)


@dataclass
class Profile:
    login: str = ""
    global_tier: str = ""  # the best of the three, which the page leads with
    persona: str = ""  # "Influencer"
    ranks: dict[str, Rank] = field(default_factory=dict)
    fetched_at: float = 0.0

    def rank(self, kind: str) -> Rank | None:
        return self.ranks.get(kind)

    def __str__(self) -> str:
        stars = self.ranks.get("s")
        standing = f", stars #{stars.position:,}" if stars else ""
        return f"{self.login} is {self.global_tier or 'unranked'}{standing}"


def default_path() -> Path:
    """The first login file that exists, else where one should be made."""
    for candidate in (CONFIG_PATH, LOCAL_PATH):
        if candidate.exists():
            return candidate
    return CONFIG_PATH


def read_login(path: Path | None = None) -> str | None:
    """The GitHub login to rank, or None -- which switches the feature off."""
    path = default_path() if path is None else path
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None  # not configured is the normal case, not a problem

    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            return line
    return None


# --- fetching ------------------------------------------------------------
#
# geckodriver speaks plain HTTP JSON, so driving it needs urllib and nothing
# else -- a selenium dependency would dwarf the rest of this program.

# Snap builds can only write a profile under the user's snap directory, so the
# binary decides where its profile has to live. Ordering matters: geckodriver
# is itself usually a snap here, and a confined driver cannot see /usr/lib.
SNAP_HOME = Path.home() / "snap" / "firefox" / "common"
FIREFOX_BINARIES = (
    (Path("/snap/firefox/current/usr/lib/firefox/firefox"), SNAP_HOME),
    (Path("/usr/lib/firefox/firefox"), CACHE_DIR),
)


def _binaries() -> list[tuple[Path, Path]]:
    found = [(binary, parent) for binary, parent in FIREFOX_BINARIES if binary.exists()]
    resolved = shutil.which("firefox")
    if resolved:
        binary = Path(resolved).resolve()
        if binary.is_file() and not any(binary == known for known, _ in found):
            found.append((binary, CACHE_DIR))
    return found


class _Driver:
    """The four WebDriver calls this needs, over geckodriver's HTTP API."""

    def __init__(self, port: int = DRIVER_PORT):
        self.port = port
        self.process = None
        self.session = None

    def _call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=DRIVER_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode(errors="replace")[:200]) from None

    def start(self) -> None:
        self.process = subprocess.Popen(
            ["geckodriver", "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                self._call("GET", "/status")
                return
            except (OSError, RuntimeError):
                time.sleep(0.25)
        raise RuntimeError("geckodriver did not come up")

    def open_session(self, binary: Path, profile: Path) -> None:
        profile.mkdir(parents=True, exist_ok=True)
        capabilities = {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "firefox",
                    "moz:firefoxOptions": {
                        "binary": str(binary),
                        "args": ["-headless", "-profile", str(profile)],
                    },
                }
            }
        }
        self.session = self._call("POST", "/session", capabilities)["value"]["sessionId"]

    def get(self, url: str) -> None:
        self._call("POST", f"/session/{self.session}/url", {"url": url})

    def source(self) -> str:
        return self._call("GET", f"/session/{self.session}/source")["value"]

    def close(self) -> None:
        if self.session is not None:
            try:
                self._call("DELETE", f"/session/{self.session}")
            except (OSError, RuntimeError):
                pass
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def fetch_html(login: str) -> str:
    """Render the ranks page in headless Firefox and return its DOM."""
    if shutil.which("geckodriver") is None:
        raise RuntimeError("geckodriver is not installed -- gitranks needs a real browser")
    candidates = _binaries()
    if not candidates:
        raise RuntimeError("no firefox found -- gitranks needs a real browser")

    driver = _Driver()
    driver.start()
    try:
        for index, (binary, parent) in enumerate(candidates):
            try:
                driver.open_session(binary, parent / "gitranks-profile")
                break
            except RuntimeError:
                # A confined driver rejects a binary or a profile it cannot
                # reach; the next candidate pairs them differently.
                if index == len(candidates) - 1:
                    raise

        driver.get(PROFILE_URL.format(login=login))
        # The challenge reloads the page under us, so poll for the real thing
        # rather than guessing at how long it takes today.
        deadline = time.monotonic() + PAGE_TIMEOUT
        while True:
            html = driver.source()
            if READY_TEXT in html:
                return html
            if time.monotonic() >= deadline:
                raise RuntimeError("gitranks did not render (still behind the challenge?)")
            time.sleep(2)
    finally:
        driver.close()


# --- parsing -------------------------------------------------------------

TAGS = re.compile(r"<(script|style|svg|noscript)\b.*?</\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
TIER = r"[A-Za-z]+(?: \d+)?"


def _visible_text(html: str) -> str:
    """The page as one line of readable text, which is all the numbers need."""
    import html as html_module

    text = TAGS.sub(" ", html)
    text = TAG.sub("\n", text)
    return " ".join(html_module.unescape(text).split())


def _number(text: str) -> int:
    """"47,939" or "1.6M" -- the page uses both, sometimes side by side."""
    text = text.strip().replace(",", "")
    scale = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(text[-1:].upper())
    if scale is None:
        return int(float(text)) if text else 0
    return int(float(text[:-1]) * scale)


def _parse_card(kind: str, text: str) -> Rank:
    rank = Rank(kind=kind)

    tier = re.match(rf"\s*({TIER})", text)
    if tier:
        rank.tier = tier.group(1)

    goal = re.search(rf"([\d,]+) \w+ to reach ({TIER})", text)
    if goal:
        rank.to_next, rank.next_tier = _number(goal.group(1)), goal.group(2)

    position = re.search(r"Position:\s*([\d,]+)\s*/\s*([\d.,]+[KMB]?)", text)
    if position:
        rank.position, rank.ranked = _number(position.group(1)), _number(position.group(2))

    top = re.search(r"Top (\d+)%", text)
    if top:
        rank.top_percent = int(top.group(1))

    change = re.search(r"This month change:\s*([↑↓])\s*([\d,]+)", text)
    if change:
        moved = _number(change.group(2))
        # Up the leaderboard is a smaller position number, but the page draws
        # it as a gain, so keep its sign rather than the position's.
        rank.month_change = moved if change.group(1) == "↑" else -moved

    score = re.search(r"Total (?:stars|followers):\s*([\d,]+)", text)
    if score:
        rank.score = _number(score.group(1))
    return rank


def parse(html: str, login: str) -> Profile:
    """Pull the three rank cards out of a rendered ranks page."""
    text = _visible_text(html)
    profile = Profile(login=login, fetched_at=time.time())

    head, _, cards = text.partition("Rank breakdown")
    globally = re.search(rf"Global Rank ({TIER})", head)
    if globally:
        profile.global_tier = globally.group(1)
    persona = re.search(r"Persona (\w+)", head)
    if persona:
        profile.persona = persona.group(1)

    # Each card runs until the next heading, so cut at the one that follows.
    headings = [(cards.find(title), kind, title) for kind, title in CARDS.items()]
    starts = sorted(start for start, _, _ in headings if start >= 0)
    for start, kind, title in headings:
        if start < 0:
            continue
        following = [nxt for nxt in starts if nxt > start]
        end = following[0] if following else len(cards)
        profile.ranks[kind] = _parse_card(kind, cards[start + len(title) : end])

    if not profile.ranks:
        raise ValueError("no rank cards on the page")
    return profile


# --- caching -------------------------------------------------------------


def cache_path(login: str) -> Path:
    return CACHE_DIR / f"gitranks-{login.lower()}.json"


def load(login: str) -> Profile | None:
    try:
        data = json.loads(cache_path(login).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        ranks = {kind: Rank(**values) for kind, values in data.pop("ranks", {}).items()}
        return Profile(ranks=ranks, **data)
    except TypeError:  # a cache written by an older, differently shaped version
        return None


def save(profile: Profile) -> None:
    path = cache_path(profile.login)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(profile), indent=1))


def ranks(login: str, refresh: float = REFRESH_SECONDS) -> Daily:
    """A once-a-day gitranks scrape for `login`, cached on disk."""
    return Daily(
        "gitranks",
        lambda: parse(fetch_html(login), login),
        lambda: load(login),
        save,
        refresh,
    )


def summary(profile: Profile | None) -> str:
    if profile is None:
        return "gitranks --"
    parts = [f"gitranks {profile.global_tier or '--'}"]
    for kind in CARDS:
        rank = profile.ranks.get(kind)
        if rank is not None:
            parts.append(f"{kind.upper()} #{rank.position:,} top {rank.top_percent}%")
    return "  ".join(parts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    login = read_login()
    if not login:
        raise SystemExit(f"no login: mkdir -p {CONFIG_DIR} && echo YOURLOGIN > {CONFIG_PATH}")
    profile = parse(fetch_html(login), login)
    save(profile)
    print(f"{profile.login}: Global Rank {profile.global_tier}  ({profile.persona})")
    for kind, title in CARDS.items():
        rank = profile.ranks.get(kind)
        if rank is None:
            continue
        print(
            f"{title:<17} {rank.tier:<10} #{rank.position:>9,} / {rank.ranked:,}"
            f"  top {rank.top_percent}%  month {rank.month_change:+,}"
            f"  score {rank.score:,}  ({rank.to_next} to {rank.next_tier})"
        )
