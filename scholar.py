"""Citation counts from the Google Scholar profile in google-scholar.txt.

Unlike gitranks this needs no browser: the profile page is served to a plain
HTTPS request, and the summary table it carries -- citations, h-index and
i10-index, each all-time and over the recent window -- is the whole point of
the scrape. Once a day is both polite and more often than the numbers move.

With no google-scholar.txt there is no profile, and nothing here is fetched.
"""

import html as html_module
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from daily import CACHE_DIR, REFRESH_SECONDS, Daily
from repos import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "google-scholar.txt"
LOCAL_PATH = Path(__file__).with_name("google-scholar.txt")
EXAMPLE_PATH = Path(__file__).with_name("google-scholar.txt.example")

PROFILE_URL = "https://scholar.google.com/citations?user={user}&hl=en"
TIMEOUT = 20.0
# Scholar serves nothing to a default urllib user agent, and a bot that names
# itself gets a captcha, so ask the way a browser would.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"

# Profile ids are the 12 character key in the ?user= parameter of the URL.
USER_ID = re.compile(r"[0-9A-Za-z_-]{12}")

logger = logging.getLogger("claude-ipixel")


@dataclass
class Citations:
    """The six numbers of a Scholar profile's summary table, plus who it is."""

    user: str = ""
    name: str = ""
    citations: int = 0
    h_index: int = 0
    i10_index: int = 0
    # The same three over Scholar's rolling recent window, whose first year it
    # labels the column with ("Since 2021").
    recent_citations: int = 0
    recent_h_index: int = 0
    recent_i10_index: int = 0
    since: str = ""
    fetched_at: float = 0.0

    def __str__(self) -> str:
        return f"{self.name or self.user}: {self.citations:,} citations, h-index {self.h_index}"


def default_path() -> Path:
    """The first profile file that exists, else where one should be made."""
    for candidate in (CONFIG_PATH, LOCAL_PATH):
        if candidate.exists():
            return candidate
    return CONFIG_PATH


def read_user(path: Path | None = None) -> str | None:
    """The profile id to scrape, or None -- which switches the feature off.

    Takes either the bare id or a pasted profile URL, because the URL is what
    the browser has in it when you go looking for the id.
    """
    path = default_path() if path is None else path
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None  # not configured is the normal case, not a problem

    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        inside_url = re.search(r"[?&]user=([^&\s]+)", line)
        candidate = inside_url.group(1) if inside_url else line
        if not USER_ID.fullmatch(candidate):
            logger.warning("%s: %r is not a Scholar profile id or URL", path, line)
            return None
        return candidate
    return None


def fetch_html(user: str) -> str:
    request = urllib.request.Request(
        PROFILE_URL.format(user=user),
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def _text(markup: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", markup)).strip()


def _number(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def parse(html: str, user: str) -> Citations:
    """Pull the summary table out of a Scholar profile page."""
    table = re.search(r'id="gsc_rsb_st".*?</table>', html, re.S)
    if table is None:
        raise ValueError("no citation table on the page (captcha or unknown profile?)")

    rows = {}
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(0), re.S):
        cells = [_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) == 3:
            rows[cells[0].lower()] = (cells[1], cells[2])

    name = re.search(r'id="gsc_prf_in"[^>]*>(.*?)<', html)
    # The header of the second column is "Since <year>", which is the only
    # place the window's start is stated.
    since = rows.get("", ("", ""))[1]

    citations = Citations(
        user=user,
        name=_text(name.group(1)) if name else "",
        since=since.replace("Since", "").strip(),
        fetched_at=time.time(),
    )
    for key, (total, recent) in (
        ("citations", ("citations", "recent_citations")),
        ("h-index", ("h_index", "recent_h_index")),
        ("i10-index", ("i10_index", "recent_i10_index")),
    ):
        values = rows.get(key)
        if values:
            setattr(citations, total, _number(values[0]))
            setattr(citations, recent, _number(values[1]))

    if not citations.citations:
        raise ValueError("no citation count on the page")
    return citations


# --- caching -------------------------------------------------------------


def cache_path(user: str) -> Path:
    return CACHE_DIR / f"scholar-{user}.json"


def load(user: str) -> Citations | None:
    try:
        return Citations(**json.loads(cache_path(user).read_text()))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def save(citations: Citations) -> None:
    path = cache_path(citations.user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(citations), indent=1))


def scholar(user: str, refresh: float = REFRESH_SECONDS) -> Daily:
    """A once-a-day Scholar scrape for `user`, cached on disk."""
    return Daily(
        "scholar",
        lambda: parse(fetch_html(user), user),
        lambda: load(user),
        save,
        refresh,
    )


def summary(citations: Citations | None) -> str:
    if citations is None:
        return "scholar --"
    return (
        f"scholar {citations.citations:,} cites"
        f" (h{citations.h_index} i10-{citations.i10_index})"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    user = read_user()
    if not user:
        raise SystemExit(f"no profile: mkdir -p {CONFIG_DIR} && echo YOURSCHOLARID > {CONFIG_PATH}")
    found = parse(fetch_html(user), user)
    save(found)
    print(f"{found.name} ({found.user})")
    print(f"{'':<12}{'All':>8}{'Since ' + found.since:>12}")
    for label, total, recent in (
        ("citations", found.citations, found.recent_citations),
        ("h-index", found.h_index, found.recent_h_index),
        ("i10-index", found.i10_index, found.recent_i10_index),
    ):
        print(f"{label:<12}{total:>8,}{recent:>12,}")
