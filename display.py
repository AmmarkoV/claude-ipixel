"""Render the usage panel as an image for an iPixel matrix.

Panel sizes range from 32x16 to 448x32 (plus a square 64x64), so the layout is
derived from the target dimensions rather than hard-coded. Text uses a
hand-rolled 3x5 bitmap font -- PIL's bundled fonts are far too tall for a 16
pixel high panel split into two rows -- scaled up by whole pixels where there
is room. Panels too narrow for a readable bar show the numbers alone.
"""

from dataclasses import dataclass

from PIL import Image

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
    " ": ("000", "000", "000", "000", "000"),
    "A": ("111", "101", "111", "101", "101"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "111", "100", "111"),
    "H": ("101", "101", "111", "101", "101"),
    "R": ("111", "101", "111", "110", "101"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
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
    row_y: tuple[int, int]
    bar_x: int
    bar_w: int
    bar_h: int
    show_bar: bool


def layout_for(width: int, height: int) -> Layout:
    """Fit two label/bar/value rows into a panel of the given size."""
    row_h = height // 2
    label_w, value_w = LABEL_CHARS * ADVANCE - 1, VALUE_CHARS * ADVANCE - 1
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
    top = (row_h - band) // 2
    return Layout(
        scale=scale,
        row_y=(top, row_h + top),
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


def _arrow_for(space: int, scale: int) -> tuple[tuple[str, ...], int] | None:
    """Biggest arrow that fits in `space` rows, or None if none does."""
    for rows in ARROWS:
        for candidate in range(scale, 0, -1):
            if len(rows) * candidate <= space:
                return rows, candidate
    return None


def _draw_pace_arrow(
    image: Image.Image, layout: Layout, limit: Limit, window: float, bar_y: int, space: int
) -> None:
    """Mark the even-spend point on a bar, in the rows immediately above it."""
    fraction = pace_fraction(limit, window)
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
    _blit(image, rows, x, bar_y - len(rows) * scale, COLOR_PACE, scale)


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


def _draw_row(image: Image.Image, layout: Layout, label: str, limit: Limit, y: int) -> None:
    color = _state_color(limit)
    draw_text(image, label, 0, y, COLOR_LABEL, layout.scale)

    if limit.exhausted:
        # Out of quota: show time until the window rolls over instead of "0%".
        value, fraction = _format_countdown(limit.seconds_until_reset()), 0.0
    else:
        value, fraction = f"{round(limit.remaining)}%", limit.remaining / 100.0

    if layout.show_bar:
        _draw_bar(image, layout, y, fraction, color)

    draw_text(image, value, image.width - text_width(value, layout.scale), y, color, layout.scale)


def render(usage: Usage, width: int = DEFAULT_SIZE[0], height: int = DEFAULT_SIZE[1]) -> Image.Image:
    image = Image.new("RGB", (width, height), (0, 0, 0))
    layout = layout_for(width, height)
    _draw_row(image, layout, "5H", usage.session, layout.row_y[0])
    _draw_row(image, layout, "7D", usage.week, layout.row_y[1])
    _draw_pace_arrow(
        image, layout, usage.session, SESSION_SECONDS, layout.row_y[0], layout.row_y[0]
    )
    _draw_pace_arrow(
        image,
        layout,
        usage.week,
        WEEK_SECONDS,
        layout.row_y[1],
        layout.row_y[1] - layout.row_y[0] - layout.bar_h,
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
