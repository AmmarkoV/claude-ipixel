"""Render the usage panel as an image for an iPixel matrix.

Panel sizes range from 32x16 to 448x32 (plus a square 64x64), so the layout is
derived from the target dimensions rather than hard-coded. Text uses a
hand-rolled 3x5 bitmap font -- PIL's bundled fonts are far too tall for a 16
pixel high panel split into two rows -- scaled up by whole pixels where there
is room. Panels too narrow for a readable bar show the numbers alone.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from PIL import Image

from gitranks import Profile
from repos import DayStats
from scholar import Citations
from usage import Limit, Usage

DEFAULT_SIZE = (64, 20)

GLYPH_W, GLYPH_H, ADVANCE = 3, 5, 4
LABEL_CHARS = 2  # "5H", "7D"
VALUE_CHARS = 4  # widest value we ever draw: "100%", "144H", "2:22"

SESSION_SECONDS = 5 * 3600
WEEK_SECONDS = 7 * 24 * 3600

COLOR_LABEL = (110, 110, 110)
COLOR_FRAME = (48, 48, 48)
COLOR_GOOD = (0, 224, 90)
COLOR_WARN = (255, 176, 0)
COLOR_LOW = (255, 80, 0)
COLOR_EMPTY = (255, 0, 0)
COLOR_PACE = (255, 0, 0)
COLOR_PACE_WORK = (255, 255, 0)  # kept off COLOR_WARN so it reads apart from an amber bar
COLOR_ADD = (0, 224, 90)
COLOR_DEL = (255, 80, 0)
COLOR_COMMIT = (80, 160, 255)

# The yellow 7D marker paces the week against working hours alone: Mon-Fri,
# 09:00-17:00 local, rather than evenly around the clock.
WORK_DAYS = frozenset(range(5))  # Monday..Friday
WORK_HOURS = (9, 17)
WEEK_WORK_SECONDS = len(WORK_DAYS) * (WORK_HOURS[1] - WORK_HOURS[0]) * 3600

# Points down at where a bar would stand on an even spend. The top row has only
# the panel margin above it to work with, so shorter markers are kept for tight
# fits, down to a plain tick on the 16 pixel high panels.
ARROWS = (#("11111", "01110", "00100"), #<- have both arrows be the same..
          ("111", "010"), ("111",))

FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "%": ("101", "001", "010", "100", "101"),
    ":": ("000", "010", "000", "010", "000"),
    "-": ("000", "000", "111", "000", "000"),
    "+": ("000", "010", "111", "010", "000"),
    " ": ("000", "000", "000", "000", "000"),
    # The whole alphabet, because repository labels are initials of any name.
    "A": ("111", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("111", "100", "100", "100", "111"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "111", "100", "111"),
    "F": ("111", "100", "111", "100", "100"),
    "G": ("111", "100", "101", "101", "111"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "111"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("110", "101", "101", "101", "101"),
    "O": ("111", "101", "101", "101", "111"),
    "P": ("111", "101", "111", "100", "100"),
    "Q": ("111", "101", "101", "111", "001"),
    "R": ("111", "101", "111", "110", "101"),
    "S": ("111", "100", "111", "001", "111"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
}


def text_width(text: str, scale: int = 1) -> int:
    if not text:
        return 0
    return (len(text) * ADVANCE - 1) * scale


def _blit(image: Image.Image, rows: tuple[str, ...], x: int, y: int, color, scale: int) -> None:
    pixels = image.load()
    for row, bits in enumerate(rows):
        for col, bit in enumerate(bits):
            if bit != "1":
                continue
            for dy in range(scale):
                for dx in range(scale):
                    px, py = x + col * scale + dx, y + row * scale + dy
                    if 0 <= px < image.width and 0 <= py < image.height:
                        pixels[px, py] = color


def draw_text(image: Image.Image, text: str, x: int, y: int, color, scale: int = 1) -> None:
    for char in text:
        _blit(image, FONT.get(char, FONT[" "]), x, y, color, scale)
        x += ADVANCE * scale


@dataclass
class Layout:
    scale: int
    row_y: tuple[int, ...]
    bar_x: int
    bar_w: int
    bar_h: int
    show_bar: bool


def layout_for(
    width: int,
    height: int,
    rows: int = 2,
    label_chars: int = LABEL_CHARS,
    value_chars: int = VALUE_CHARS,
) -> Layout:
    """Fit `rows` label/bar/value rows into a panel of the given size."""
    row_h = height // rows
    label_w, value_w = label_chars * ADVANCE - 1, value_chars * ADVANCE - 1
    gap = 3

    # Scale up only as far as both the row height and the text width allow...
    largest = max(1, min((row_h - 2) // GLYPH_H, width // (label_w + gap + value_w)))

    def bar_at(scale: int) -> tuple[int, int]:
        bar_x = (label_w + gap) * scale
        return bar_x, width - bar_x - (gap + value_w) * scale

    # ...then trade text size back for a bar, since the bar is the point. Only a
    # panel too narrow to fit one at any scale falls back to numbers alone.
    scale = largest
    for candidate in range(largest, 0, -1):
        if bar_at(candidate)[1] >= 10 * candidate:
            scale = candidate
            break

    bar_x, bar_w = bar_at(scale)
    band = GLYPH_H * scale
    # Centre the block of rows, so a row count that does not divide the panel
    # height evenly leaves its slack split top and bottom rather than all below.
    top = (height - rows * row_h) // 2 + (row_h - band) // 2
    return Layout(
        scale=scale,
        row_y=tuple(top + row * row_h for row in range(rows)),
        bar_x=bar_x,
        bar_w=bar_w,
        bar_h=band,
        show_bar=bar_w >= 10 * scale,
    )


def _state_color(limit: Limit):
    if limit.exhausted:
        return COLOR_EMPTY
    if limit.remaining >= 50:
        return COLOR_GOOD
    if limit.remaining >= 20:
        return COLOR_WARN
    return COLOR_LOW


def _format_countdown(seconds: float | None) -> str:
    """Time until reset, at most 4 characters wide: "2:07" or "66H"."""
    if seconds is None:
        return "-:--"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours >= 24:
        return f"{hours}H"  # "D" is too close to "0" at 3px wide to read as days
    return f"{hours}:{minutes:02d}"


def pace_fraction(limit: Limit, window_seconds: float) -> float | None:
    """Where a bar would stand if its quota were spent evenly: the share of the
    rolling window still to run. None if the reset is unknown."""
    seconds = limit.seconds_until_reset()
    if seconds is None:
        return None
    return min(1.0, seconds / window_seconds)


def _work_seconds(start: datetime, end: datetime) -> float:
    """Working hours between two naive local datetimes."""
    total = 0.0
    day = start.date()
    while day <= end.date():
        if day.weekday() in WORK_DAYS:
            lo = datetime.combine(day, time(WORK_HOURS[0]))
            hi = datetime.combine(day, time(WORK_HOURS[1]))
            total += max(0.0, (min(end, hi) - max(start, lo)).total_seconds())
        day += timedelta(days=1)
    return total


def work_pace_fraction(limit: Limit, now: datetime | None = None) -> float | None:
    """Where the week bar would stand if its quota were spent evenly across
    working hours only: the share of the window's working time still to run.
    None if the reset is unknown."""
    if limit.resets_at is None:
        return None
    start = (now or datetime.now(timezone.utc)).astimezone().replace(tzinfo=None)
    end = limit.resets_at.astimezone().replace(tzinfo=None)
    return min(1.0, _work_seconds(start, end) / WEEK_WORK_SECONDS)


def _arrow_for(space: int, scale: int) -> tuple[tuple[str, ...], int] | None:
    """Biggest arrow that fits in `space` rows, or None if none does."""
    for rows in ARROWS:
        for candidate in range(scale, 0, -1):
            if len(rows) * candidate <= space:
                return rows, candidate
    return None


def _draw_pace_arrow(
    image: Image.Image, layout: Layout, fraction: float | None, color, bar_y: int, space: int
) -> None:
    """Mark a pace point on a bar, in the rows immediately above it."""
    if fraction is None or not layout.show_bar:
        return
    arrow = _arrow_for(space, layout.scale)
    if arrow is None:
        return  # nothing above the bar to draw into

    rows, scale = arrow
    inner_x = layout.bar_x + layout.scale  # inside the bar frame
    inner_w = layout.bar_w - 2 * layout.scale
    tip = inner_x + round(inner_w * fraction)
    width = len(rows[0]) * scale
    x = min(max(tip - (len(rows[0]) // 2) * scale, 0), image.width - width)
    _blit(image, rows, x, bar_y - len(rows) * scale, color, scale)


def _draw_bar(image: Image.Image, layout: Layout, y: int, fraction: float, color) -> None:
    pixels = image.load()
    thickness = layout.scale
    x0, x1 = layout.bar_x, layout.bar_x + layout.bar_w - 1
    y0, y1 = y, y + layout.bar_h - 1

    for x in range(x0, x1 + 1):
        for d in range(thickness):
            pixels[x, y0 + d] = COLOR_FRAME
            pixels[x, y1 - d] = COLOR_FRAME
    for row in range(y0, y1 + 1):
        for d in range(thickness):
            pixels[x0 + d, row] = COLOR_FRAME
            pixels[x1 - d, row] = COLOR_FRAME

    filled = round((layout.bar_w - 2 * thickness) * fraction)
    for x in range(x0 + thickness, x0 + thickness + filled):
        for row in range(y0 + thickness, y1 - thickness + 1):
            pixels[x, row] = color


def _draw_gauge(
    image: Image.Image,
    layout: Layout,
    label: str,
    value: str,
    fraction: float,
    color,
    y: int,
) -> None:
    """One `label · bar · value` row, the shape every view is built from."""
    draw_text(image, label, 0, y, COLOR_LABEL, layout.scale)
    if layout.show_bar:
        _draw_bar(image, layout, y, fraction, color)
    draw_text(image, value, image.width - text_width(value, layout.scale), y, color, layout.scale)


def _draw_row(image: Image.Image, layout: Layout, label: str, limit: Limit, y: int) -> None:
    if limit.exhausted:
        # Out of quota: show time until the window rolls over instead of "0%".
        value, fraction = _format_countdown(limit.seconds_until_reset()), 0.0
    else:
        value, fraction = f"{round(limit.remaining)}%", limit.remaining / 100.0
    _draw_gauge(image, layout, label, value, fraction, _state_color(limit), y)


def render(usage: Usage, width: int = DEFAULT_SIZE[0], height: int = DEFAULT_SIZE[1]) -> Image.Image:
    image = Image.new("RGB", (width, height), (0, 0, 0))
    layout = layout_for(width, height)
    _draw_row(image, layout, "5H", usage.session, layout.row_y[0])
    _draw_row(image, layout, "7D", usage.week, layout.row_y[1])
    week_space = layout.row_y[1] - layout.row_y[0] - layout.bar_h
    _draw_pace_arrow(
        image,
        layout,
        pace_fraction(usage.session, SESSION_SECONDS),
        COLOR_PACE,
        layout.row_y[0],
        layout.row_y[0],
    )
    # Yellow first, so the round-the-clock arrow stays on top where they overlap.
    _draw_pace_arrow(
        image, layout, work_pace_fraction(usage.week), COLOR_PACE_WORK, layout.row_y[1], week_space
    )
    _draw_pace_arrow(
        image,
        layout,
        pace_fraction(usage.week, WEEK_SECONDS),
        COLOR_PACE,
        layout.row_y[1],
        week_space,
    )
    return image


def render_message(
    text: str,
    width: int = DEFAULT_SIZE[0],
    height: int = DEFAULT_SIZE[1],
    color=COLOR_EMPTY,
) -> Image.Image:
    """Full-panel message, used for error states."""
    image = Image.new("RGB", (width, height), (0, 0, 0))
    scale = 1
    while text_width(text, scale + 1) <= width and GLYPH_H * (scale + 1) <= height - 2:
        scale += 1
    draw_text(
        image,
        text,
        (width - text_width(text, scale)) // 2,
        (height - GLYPH_H * scale) // 2,
        color,
        scale,
    )
    return image


def _render_figure(text: str, marks, color, width: int, height: int) -> Image.Image:
    """One big number with small marks down its left saying what it counts.

    The marks are sized off the panel alone, never off the number, so working
    out how much room the number has left is not circular. Each is a
    `(bitmap, colour, at_top)` triple; `at_top` hangs it from the top edge
    rather than centring it on the number.
    """
    image = Image.new("RGB", (width, height), (0, 0, 0))
    columns = sum(len(rows[0]) + 2 for rows, _, _ in marks)  # each mark and its gap
    mark = max(1, min(height // 16, width // 32))
    # Never let the marks eat more than half the panel: on a square one they
    # would otherwise leave the number smaller than the marks beside it.
    while mark > 1 and columns * mark > width // 2:
        mark -= 1
    inset = columns * mark
    if inset + text_width(text, 1) > width:
        inset, mark = 0, 0  # too narrow to spare the columns; the number wins

    x = 0
    for rows, mark_color, at_top in marks if mark else ():
        _blit(image, rows, x, 0 if at_top else (height - len(rows) * mark) // 2, mark_color, mark)
        x += (len(rows[0]) + 2) * mark

    scale = 1
    while (
        text_width(text, scale + 1) <= width - inset
        and GLYPH_H * (scale + 1) <= height - 2
    ):
        scale += 1
    draw_text(
        image,
        text,
        inset + (width - inset - text_width(text, scale)) // 2,
        (height - GLYPH_H * scale) // 2,
        color,
        scale,
    )
    return image


# --- today's git activity ------------------------------------------------
#
# Four ways of looking at the same scan, so there is something to choose
# between before settling on one: `net` for a single glanceable number,
# `churn` for the shape of the day's work, `repos` and `commits` for where it
# went. All of them are drawn from the same row shape as the quota view.

GIT_VIEWS = ("net", "churn", "repos", "commits")
REPO_LABEL_CHARS = 3
COUNT_CHARS = 5  # "+9999", "-123K"


def _format_count(value: int) -> str:
    """A line count in at most four characters: 9999, then 10K, then 1M."""
    value = abs(value)
    if value < 10000:
        return str(value)
    if value < 1000000:
        return f"{value // 1000}K"
    return f"{value // 1000000}M"


def _signed(value: int) -> str:
    return ("-" if value < 0 else "+") + _format_count(value)


def render_net(stats: DayStats, width: int, height: int) -> Image.Image:
    """Today's net line count, as large as the panel will take it."""
    return render_message(
        _signed(stats.net), width, height, COLOR_ADD if stats.net >= 0 else COLOR_DEL
    )


def render_churn(stats: DayStats, width: int, height: int) -> Image.Image:
    """Lines added over lines removed today, summed across every repository.

    The two bars split one total, so a day spent deleting reads at a glance.
    """
    image = Image.new("RGB", (width, height), (0, 0, 0))
    layout = layout_for(width, height, label_chars=1, value_chars=COUNT_CHARS)
    total = stats.churn or 1
    rows = (("+", stats.added, COLOR_ADD), ("-", stats.removed, COLOR_DEL))
    for (label, count, color), y in zip(rows, layout.row_y):
        _draw_gauge(image, layout, label, _format_count(count), count / total, color, y)
    return image


def render_repos(
    stats: DayStats, width: int, height: int, by_commits: bool = False
) -> Image.Image:
    """A row per repository -- initials, share of the day, and its figure.

    Busiest first, so the repositories that fit are the ones worth seeing; the
    bar is each one's share of the leader rather than of the total, which keeps
    a lone active repository from drawing a full bar next to three empty ones.
    """
    if not stats.repos:
        return render_message("NONE", width, height, COLOR_LABEL)

    key = (lambda repo: repo.commits) if by_commits else (lambda repo: repo.churn)
    fits = max(1, height // (GLYPH_H + 1))
    repos = sorted(stats.repos, key=key, reverse=True)[:fits]

    image = Image.new("RGB", (width, height), (0, 0, 0))
    layout = layout_for(
        width, height, len(repos), label_chars=REPO_LABEL_CHARS, value_chars=COUNT_CHARS
    )
    leader = max(key(repo) for repo in repos) or 1
    for repo, y in zip(repos, layout.row_y):
        if by_commits:
            value, color = _format_count(repo.commits), COLOR_COMMIT
        else:
            value, color = _signed(repo.net), COLOR_ADD if repo.net >= 0 else COLOR_DEL
        _draw_gauge(image, layout, repo.label, value, key(repo) / leader, color, y)
    return image


def render_git(view: str, stats: DayStats, width: int, height: int) -> Image.Image:
    if view == "net":
        return render_net(stats, width, height)
    if view == "churn":
        return render_churn(stats, width, height)
    return render_repos(stats, width, height, by_commits=view == "commits")


def git_has_content(view: str, stats: DayStats) -> bool:
    """Whether today's scan has anything for this view to draw. A day with no
    committed work would otherwise rotate a screen of zeros onto the panel.
    """
    if view == "commits":
        return stats.commits > 0
    if view == "repos":
        return any(repo.churn for repo in stats.repos)
    if view == "net":
        return stats.net != 0  # a balanced day draws "+0"; `churn` still has it
    return stats.churn > 0


# --- gitranks standing ---------------------------------------------------
#
# Three ways of looking at the daily scrape: `rank` for where the three
# rankings stand against everyone else, `tier` for the one word the profile
# page leads with, `stars` for the figure the rankings are built on. The first
# reuses the row shape above, the other two fill the panel.

GITRANKS_VIEWS = ("rank", "tier", "stars")
RANK_LABEL_CHARS = 5  # "STARS", "CONTR", "FOLLW"
PERCENT_CHARS = 3  # "50%"

COLOR_TIER = (200, 120, 255)
COLOR_STAR = (255, 190, 0)
COLOR_OCTOCAT = (170, 170, 170)

# The two marks the star count sits behind: the octocat says where the figure
# comes from, the star says what is being counted. Drawn side by side, small,
# down the left of the number.
OCTOCAT = (
    "01100000110",
    "01111111110",
    "11111111111",
    "11011111011",
    "11111111111",
    "11111111111",
    "11111111111",
    "01111111110",
    "00111111100",
    "00110011000",
    "00110011000",
)
STAR = (
    "000010000",
    "000111000",
    "011111110",
    "111111111",
    "011111110",
    "001111100",
    "011111110",
    "011000110",
    "110000011",
)


def _rank_color(top_percent: int):
    if top_percent and top_percent <= 10:
        return COLOR_GOOD
    if top_percent and top_percent <= 50:
        return COLOR_WARN
    return COLOR_LOW


def render_rank(profile: Profile, width: int, height: int) -> Image.Image:
    """A row per ranking -- stars, contributions, followers.

    The bar is the share of ranked profiles standing below this one, so a full
    bar means the top of the board rather than the bottom of it.
    """
    ranks = [rank for rank in (profile.rank(kind) for kind in ("s", "c", "f")) if rank]
    if not ranks:
        return render_message("NONE", width, height, COLOR_LABEL)

    ranks = ranks[: max(1, height // (GLYPH_H + 1))]
    image = Image.new("RGB", (width, height), (0, 0, 0))
    layout = layout_for(
        width, height, len(ranks), label_chars=RANK_LABEL_CHARS, value_chars=PERCENT_CHARS
    )
    for rank, y in zip(ranks, layout.row_y):
        _draw_gauge(
            image,
            layout,
            rank.label,
            f"{rank.top_percent}%" if rank.top_percent else "--",
            rank.percentile,
            _rank_color(rank.top_percent),
            y,
        )
    return image


def render_tier(profile: Profile, width: int, height: int) -> Image.Image:
    """The overall tier, as large as the panel will take it."""
    tier = profile.global_tier.upper()
    if not tier:
        return render_message("NONE", width, height, COLOR_LABEL)
    return render_message(tier, width, height, COLOR_TIER)


def render_stars(profile: Profile, width: int, height: int) -> Image.Image:
    """Total GitHub stars, as large as the space beside the marks allows."""
    stars = profile.rank("s")
    if stars is None or not stars.score:
        return render_message("NONE", width, height, COLOR_LABEL)
    return _render_figure(
        _format_count(stars.score),
        ((OCTOCAT, COLOR_OCTOCAT, False), (STAR, COLOR_STAR, False)),
        COLOR_STAR,
        width,
        height,
    )


def render_gitranks(view: str, profile: Profile, width: int, height: int) -> Image.Image:
    if view == "tier":
        return render_tier(profile, width, height)
    if view == "stars":
        return render_stars(profile, width, height)
    return render_rank(profile, width, height)


def gitranks_has_content(view: str, profile: Profile) -> bool:
    """Whether the scrape found a standing worth a slot."""
    if view == "tier":
        return bool(profile.global_tier)
    if view == "stars":
        stars = profile.rank("s")
        return stars is not None and stars.score > 0
    return any(rank.position for rank in profile.ranks.values())


# --- google scholar ------------------------------------------------------
#
# `cites` is the number people actually quote. `hindex` drew the two indices
# side by side and is left here, off the rotation, in case it earns its slot
# back later.

SCHOLAR_VIEWS = ("cites",)  # ("cites", "hindex")

COLOR_CITE = (0, 200, 255)
COLOR_CITE_RECENT = (0, 224, 90)
# Dimmed towards the number rather than plain grey: seven pixels in a corner
# read as a stray fault at this size unless they clearly belong to something.
COLOR_CITE_MARK = (0, 110, 140)

# A closing quotation mark: two blobs, each with its tail falling away to the
# left. It sits in the corner of the citation count to say what the number is,
# since a bare figure on a panel could be anything.
QUOTE = (
    "0110110",
    "0110110",
    "0100100",
    "1001000",
)


def render_cites(citations: Citations, width: int, height: int) -> Image.Image:
    """Total citations, as large as the space beside the quote mark allows."""
    return _render_figure(
        _format_count(citations.citations),
        ((QUOTE, COLOR_CITE_MARK, True),),
        COLOR_CITE,
        width,
        height,
    )


def render_hindex(citations: Citations, width: int, height: int) -> Image.Image:
    """h-index and i10-index, with the recent window's share as the bar.

    Off the rotation -- put "hindex" back in SCHOLAR_VIEWS to see it again.
    """
    image = Image.new("RGB", (width, height), (0, 0, 0))
    layout = layout_for(width, height, label_chars=1, value_chars=VALUE_CHARS)
    rows = (
        ("H", citations.h_index, citations.recent_h_index),
        ("I", citations.i10_index, citations.recent_i10_index),
    )
    for (label, total, recent), y in zip(rows, layout.row_y):
        _draw_gauge(
            image,
            layout,
            label,
            _format_count(total),
            recent / total if total else 0.0,
            COLOR_CITE_RECENT,
            y,
        )
    return image


def render_scholar(view: str, citations: Citations, width: int, height: int) -> Image.Image:
    if view == "hindex":
        return render_hindex(citations, width, height)
    return render_cites(citations, width, height)


def scholar_has_content(view: str, citations: Citations) -> bool:
    """Whether the profile has any figure to show."""
    if view == "hindex":
        return bool(citations.h_index or citations.i10_index)
    return citations.citations > 0
