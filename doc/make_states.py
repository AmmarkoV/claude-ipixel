"""Rebuild doc/states.png -- every view the panel can draw, one row each.

    ./venv/bin/python doc/make_states.py

The figures are frozen sample values rather than whatever is in your caches,
so the picture in the README stays put instead of shifting every time someone
regenerates it, and so this runs on a fresh clone with nothing configured.
They match the numbers quoted in the README's own examples; if you change one,
change the other.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import display  # noqa: E402
from gitranks import Profile, Rank  # noqa: E402
from repos import DayStats, RepoStats  # noqa: E402
from scholar import Citations  # noqa: E402
from usage import Limit, Usage  # noqa: E402

OUTPUT = ROOT / "doc" / "states.png"

PANEL = (64, 20)  # the size the README talks in
SCALE = 6
CAPTION_W = 330
GUTTER = 14
ROW_GAP = 10
SECTION_GAP = 30

BG = (20, 20, 20)
FG = (198, 198, 198)
DIM = (128, 128, 128)
RULE = (54, 54, 54)

FONTS = Path("/usr/share/fonts/truetype/dejavu")


def font(name: str, size: int):
    """DejaVu if it is installed, PIL's own bitmap font if it is not."""
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default()


CAPTION = font("DejaVuSans.ttf", 13)
MONO = font("DejaVuSansMono-Bold.ttf", 12)
HEADER = font("DejaVuSans-Bold.ttf", 12)


def later(**kwargs) -> datetime:
    # Countdowns truncate, so land just past the figure the caption quotes.
    return datetime.now(timezone.utc) + timedelta(seconds=40, **kwargs)


def quota(session_left, session_in, week_left, week_in) -> Usage:
    """A Usage with the two windows at the given percentages remaining."""
    return Usage(
        session=Limit(100.0 - session_left, later(**session_in)),
        week=Limit(100.0 - week_left, later(**week_in)),
    )


DAY = DayStats([
    RepoStats("magician_vision_classifier", "MVC", commits=5, added=5872, removed=99),
    RepoStats("magician_grabber_annotator", "MGA", commits=1, added=124, removed=37),
    RepoStats("magician_main_board", "MMB"),
])

RANKED = Profile(
    login="AmmarkoV",
    global_tier="Elite 1",
    persona="Influencer",
    ranks={
        "s": Rank("s", "Master 5", 47_939, 1_600_000, 3, 1_940, 843, "Elite 1", 2),
        "c": Rank("c", "Adept 2", 1_357_286, 3_200_000, 50, -12_435, 1_705, "Adept 3", 472),
        "f": Rank("f", "Elite 1", 63_602, 2_400_000, 3, 584, 189, "Elite 2", 2),
    },
)

CITED = Citations(
    user="sDOdhtwAAAAJ",
    name="Ammar Qammaz",
    citations=704,
    h_index=11,
    i10_index=13,
    recent_citations=433,
    recent_h_index=9,
    recent_i10_index=9,
    since="2021",
)

W, H = PANEL

# (flag, caption, image) per row; a row with no image is a section heading.
ROWS = [
    ("QUOTA", None, None),
    ("--view quota", "plenty left in both windows",
     display.render(quota(58, dict(hours=3), 70, dict(days=5)), W, H)),
    ("--view quota", "session low, burning faster than the clock",
     display.render(quota(15, dict(hours=1, minutes=30), 70, dict(days=5)), W, H)),
    ("--view quota", "session spent, back in 2h21",
     display.render(quota(0, dict(hours=2, minutes=21), 4, dict(days=2)), W, H)),
    ("--view quota", "both spent, week back in 66h",
     display.render(quota(0, dict(minutes=44), 0, dict(hours=66)), W, H)),

    ("TODAY'S GIT ACTIVITY", None, None),
    ("--view net", "today's net lines, every repository",
     display.render_git("net", DAY, W, H)),
    ("--view churn", "added over removed, splitting one total",
     display.render_git("churn", DAY, W, H)),
    ("--view repos", "per repository: initials, share, net lines",
     display.render_git("repos", DAY, W, H)),
    ("--view commits", "the same rows counted in commits",
     display.render_git("commits", DAY, W, H)),

    ("GITRANKS", None, None),
    ("--view rank", "bar is how far up the board you are",
     display.render_gitranks("rank", RANKED, W, H)),
    ("--view tier", "the tier the profile page leads with",
     display.render_gitranks("tier", RANKED, W, H)),

    ("GOOGLE SCHOLAR", None, None),
    ("--view cites", "total citations, under a quote mark",
     display.render_scholar("cites", CITED, W, H)),

    ("FAILURE", None, None),
    ("AUTH", "credentials missing, malformed or rejected",
     display.render_message("AUTH", W, H)),
    ("ERR", "three failures reaching the usage endpoint",
     display.render_message("ERR", W, H)),
]


def wrapped(draw: ImageDraw.ImageDraw, text: str, room: int) -> list[str]:
    """Greedy wrap to `room` pixels, so a caption never runs under a panel."""
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=CAPTION) <= room:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


panel_h = H * SCALE
height = sum(SECTION_GAP if image is None else panel_h + ROW_GAP for _, _, image in ROWS)
width = CAPTION_W + W * SCALE + GUTTER

sheet = Image.new("RGB", (width, height + ROW_GAP), BG)
draw = ImageDraw.Draw(sheet)

y = ROW_GAP // 2
for label, note, image in ROWS:
    if image is None:
        y += SECTION_GAP // 3
        draw.text((GUTTER, y), label, font=HEADER, fill=FG)
        draw.line([(GUTTER, y + 16), (width - GUTTER, y + 16)], fill=RULE)
        y += SECTION_GAP - SECTION_GAP // 3
        continue

    sheet.paste(image.resize((W * SCALE, H * SCALE), Image.NEAREST), (CAPTION_W, y))

    x = GUTTER + 8
    lines = wrapped(draw, note, CAPTION_W - x - GUTTER)
    top = y + (panel_h - (19 + 16 * len(lines))) // 2
    draw.text((x, top), label, font=MONO, fill=FG)
    for index, line in enumerate(lines):
        draw.text((x, top + 21 + index * 16), line, font=CAPTION, fill=DIM)
    y += panel_h + ROW_GAP

sheet.save(OUTPUT)
print(f"{OUTPUT.relative_to(ROOT)}  {sheet.width}x{sheet.height}")
