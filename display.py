"""Render the usage panel as an image for the 64x20 iPixel matrix.

Uses a hand-rolled 3x5 bitmap font -- PIL's bundled fonts are far too tall for
a 20 pixel high panel split into two rows.
"""

from PIL import Image

from usage import Limit, Usage

WIDTH, HEIGHT = 64, 20

GLYPH_W, GLYPH_H, ADVANCE = 3, 5, 4
ROW_Y = (2, 12)  # top of the glyph band for the session / week rows
LABEL_X = 0
BAR_X, BAR_W, BAR_H = 10, 36, 5

COLOR_LABEL = (110, 110, 110)
COLOR_FRAME = (48, 48, 48)
COLOR_GOOD = (0, 224, 90)
COLOR_WARN = (255, 176, 0)
COLOR_LOW = (255, 80, 0)
COLOR_EMPTY = (255, 0, 0)

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


def draw_text(image: Image.Image, text: str, x: int, y: int, color, scale: int = 1) -> None:
    pixels = image.load()
    for char in text:
        glyph = FONT.get(char, FONT[" "])
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px, py = x + col * scale + dx, y + row * scale + dy
                        if 0 <= px < image.width and 0 <= py < image.height:
                            pixels[px, py] = color
        x += ADVANCE * scale


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


def _draw_row(image: Image.Image, label: str, limit: Limit, y: int) -> None:
    color = _state_color(limit)
    draw_text(image, label, LABEL_X, y, COLOR_LABEL)

    # Bar frame, always drawn so both rows keep the same geometry.
    pixels = image.load()
    for x in range(BAR_X, BAR_X + BAR_W):
        pixels[x, y] = COLOR_FRAME
        pixels[x, y + BAR_H - 1] = COLOR_FRAME
    for row in range(y, y + BAR_H):
        pixels[BAR_X, row] = COLOR_FRAME
        pixels[BAR_X + BAR_W - 1, row] = COLOR_FRAME

    if limit.exhausted:
        # Out of quota: show time until the window rolls over instead of "0%".
        value = _format_countdown(limit.seconds_until_reset())
    else:
        inner_w = BAR_W - 2
        filled = round(inner_w * limit.remaining / 100.0)
        for x in range(BAR_X + 1, BAR_X + 1 + filled):
            for row in range(y + 1, y + BAR_H - 1):
                pixels[x, row] = color
        value = f"{round(limit.remaining)}%"

    draw_text(image, value, WIDTH - text_width(value), y, color)


def render(usage: Usage) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    _draw_row(image, "5H", usage.session, ROW_Y[0])
    _draw_row(image, "7D", usage.week, ROW_Y[1])
    return image


def render_message(text: str, color=COLOR_EMPTY) -> Image.Image:
    """Full-panel message, used for error states."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    scale = 2 if text_width(text, 2) <= WIDTH else 1
    draw_text(
        image,
        text,
        (WIDTH - text_width(text, scale)) // 2,
        (HEIGHT - GLYPH_H * scale) // 2,
        color,
        scale,
    )
    return image
