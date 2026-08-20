"""Draw remaining Claude session/week quota on an iPixel LED matrix."""

import argparse
import asyncio
import io
import logging
import signal
import sys
import time
from pathlib import Path

from bleak import BleakScanner
from pypixelcolor import AsyncClient

import display
import gitranks
import repos
import scholar
import usage

NAME_PREFIX = "LED_BLE_"  # panels advertise as LED_BLE_<last 4 bytes of the MAC>
SCAN_SECONDS = 8.0
POLL_SECONDS = 60
ROTATE_SECONDS = 15
RECONNECT_SECONDS = 15
FAILURES_BEFORE_ERROR = 3  # ride out transient network blips on the last frame
SHUTDOWN_SECONDS = 5  # give up on the goodbye if the panel stops answering

VIEW_QUOTA = "quota"
VIEWS = (VIEW_QUOTA,) + display.GIT_VIEWS + display.GITRANKS_VIEWS + display.SCHOLAR_VIEWS

logger = logging.getLogger("claude-ipixel")

# A single rewritten status line only makes sense on a terminal; under systemd
# stdout is the journal, where \r is noise, so there we keep plain log lines.
INTERACTIVE = sys.stdout.isatty()


class StatusLineHandler(logging.StreamHandler):
    """Wipe the in-place status line before a log record prints over it."""

    def emit(self, record: logging.LogRecord) -> None:
        if INTERACTIVE:
            self.stream.write("\r\033[K")
        super().emit(record)


def _format_reset(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d{hours:02d}h"
    return f"{hours}h{minutes:02d}m"


def _quota_status(current: usage.Usage) -> str:
    return "  ".join(
        f"{label} {limit.remaining:3.0f}% left, resets in {_format_reset(limit.seconds_until_reset())}"
        for label, limit in (("5H", current.session), ("7D", current.week))
    )


def _write_status(status: str, sent_at: float | None) -> None:
    if not INTERACTIVE:
        return
    line = f"{time.strftime('%H:%M:%S')}  {status}"
    if sent_at is not None:
        line += f"  [sent {time.strftime('%H:%M:%S', time.localtime(sent_at))}]"
    sys.stdout.write(f"\r\033[K{line}")
    sys.stdout.flush()


def _to_png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def discover_address(timeout: float = SCAN_SECONDS) -> str:
    """Find a panel by its advertised name. Raises RuntimeError if there is none."""
    logger.info("scanning %.0fs for %s* panels", timeout, NAME_PREFIX)
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    panels = [
        (advertisement.rssi, device.address, device.name)
        for device, advertisement in found.values()
        if (device.name or "").upper().startswith(NAME_PREFIX)
    ]
    if not panels:
        raise RuntimeError(f"no {NAME_PREFIX}* panel in range -- pass --address")

    panels.sort(key=lambda panel: panel[0], reverse=True)
    rssi, address, name = panels[0]
    if len(panels) > 1:
        logger.info("%d panels in range, using the closest", len(panels))
    logger.info("found %s at %s (%d dBm)", name, address, rssi)
    return address


def _rotation(views) -> tuple[str, ...]:
    """The order the panel walks its views in.

    The quota is the figure that moves fastest and the one worth watching, so
    it takes every other slot rather than coming round once per full turn:
    quota, net, quota, churn, quota, ... A rotation without it is left alone.
    """
    others = [name for name in views if name != VIEW_QUOTA]
    if VIEW_QUOTA not in views or not others:
        return tuple(views)
    return tuple(name for other in others for name in (VIEW_QUOTA, other))


class Panel:
    """Turns the usage endpoint, the repository scan and the daily scrapes
    into a frame.

    Every source is polled on its own schedule rather than once per frame, so
    rotating through views every few seconds costs no extra requests and no
    extra `git` processes -- a tick that finds the caches warm draws from
    memory alone. The two scrapes go further and refresh on a thread, so a
    page load that takes a minute never holds up a redraw.
    """

    def __init__(self, views=(VIEW_QUOTA,), scanner=None, ranks=None, cites=None,
                 interval=POLL_SECONDS, rotate=ROTATE_SECONDS):
        self.views = tuple(views)
        self.scanner = scanner
        self.ranks = ranks
        self.cites = cites
        self.interval = interval
        self.rotate = rotate
        self.consecutive_failures = 0
        self.status = "starting"
        self._usage = None
        self._error = None
        self._quota_status = "starting"
        self._fetched_at = None

    def view(self, views=None, now: float | None = None) -> str:
        """Which view this moment belongs to, straight off the wall clock."""
        order = _rotation(self.views if views is None else views)
        if len(order) == 1:
            return order[0]
        now = time.time() if now is None else now
        return order[int(now // self.rotate) % len(order)]

    def _has_content(self, view: str, stats, profile, citations) -> bool:
        """Whether a view has anything but zeros to show. An idle day, an
        unranked profile or an uncited paper is not worth a slot in the
        rotation, so those views are passed over rather than drawn empty.
        """
        if view in display.GIT_VIEWS:
            return stats is not None and display.git_has_content(view, stats)
        if view in display.GITRANKS_VIEWS:
            return profile is not None and display.gitranks_has_content(view, profile)
        if view in display.SCHOLAR_VIEWS:
            return citations is not None and display.scholar_has_content(view, citations)
        return True  # the quota always has something to say

    def _refresh_usage(self) -> None:
        """Fetch at most once per interval, keeping the outcome for later ticks."""
        if VIEW_QUOTA not in self.views:
            return  # nothing on screen needs the endpoint, so don't ask it
        now = time.monotonic()
        # The half-second of slack keeps a tick that lands a hair early from
        # silently doubling the poll interval.
        if self._fetched_at is not None and now - self._fetched_at < self.interval - 0.5:
            return
        self._fetched_at = now
        try:
            self._usage = usage.fetch()
        except usage.AuthError as exc:
            logger.error("auth failed: %s", exc)
            self._usage, self._error, self.consecutive_failures = None, "AUTH", 0
            self._quota_status = f"auth failed: {exc}"
        except Exception as exc:
            self.consecutive_failures += 1
            logger.warning("fetch failed (%d): %s", self.consecutive_failures, exc)
            self._usage = None
            # Ride out transient blips on the last frame; only a run of them errors.
            self._error = "ERR" if self.consecutive_failures >= FAILURES_BEFORE_ERROR else None
            self._quota_status = f"fetch failed ({self.consecutive_failures}): {exc}"
        else:
            self._error, self.consecutive_failures = None, 0
            self._quota_status = _quota_status(self._usage)

    def frame(self, width: int, height: int):
        self._refresh_usage()
        stats = self.scanner.stats() if self.scanner is not None else None
        profile = self.ranks.value() if self.ranks is not None else None
        citations = self.cites.value() if self.cites is not None else None

        parts = [self._quota_status] if VIEW_QUOTA in self.views else []
        if stats is not None:
            parts.append(repos.summary(stats))
        if self.ranks is not None:
            parts.append(gitranks.summary(profile))
        if self.cites is not None:
            parts.append(scholar.summary(citations))
        self.status = "  |  ".join(parts)

        # Rotate through the views with something on them; if none has, fall
        # back to the full list rather than leaving the panel with nothing.
        live = tuple(
            name for name in self.views if self._has_content(name, stats, profile, citations)
        )
        view = self.view(live or self.views)
        if view in display.GIT_VIEWS and stats is not None:
            return display.render_git(view, stats, width, height)
        if view in display.GITRANKS_VIEWS and profile is not None:
            return display.render_gitranks(view, profile, width, height)
        if view in display.SCHOLAR_VIEWS and citations is not None:
            return display.render_scholar(view, citations, width, height)
        if view != VIEW_QUOTA:
            return None  # its source has nothing yet; leave the last frame up
        if self._usage is not None:
            return display.render(self._usage, width, height)
        if self._error is not None:
            return display.render_message(self._error, width, height)
        return None  # keep whatever is on screen


async def power_off(client) -> None:
    """Blank the panel on the way out, so it never sits on a stale frame."""
    try:
        await asyncio.wait_for(client.set_power(False), timeout=SHUTDOWN_SECONDS)
        logger.info("panel powered off")
    except Exception as exc:
        logger.warning("could not power off the panel: %s", exc or type(exc).__name__)


async def run(address: str | None, panel: "Panel", tick: float) -> None:
    while True:
        try:
            # Rediscover on every reconnect: these panels use a random BLE
            # address, which changes when the device power-cycles.
            target = address or await discover_address()
            async with AsyncClient(target) as client:
                info = client.get_device_info()
                logger.info("connected to %s (%dx%d)", target, info.width, info.height)
                last_png, sent_at = None, None
                try:
                    while True:
                        image = panel.frame(info.width, info.height)
                        if image is not None:
                            png = _to_png(image)
                            if png != last_png:
                                await client.send_image_hex(png.hex(), ".png")
                                last_png, sent_at = png, time.time()
                        _write_status(panel.status, sent_at)
                        await asyncio.sleep(tick)
                except asyncio.CancelledError:
                    await power_off(client)  # still connected here, unlike outside
                    raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("device error, retrying: %s", exc)
            await asyncio.sleep(RECONNECT_SECONDS)


async def run_once(address: str | None, panel: "Panel") -> None:
    target = address or await discover_address()
    async with AsyncClient(target) as client:
        info = client.get_device_info()
        image = panel.frame(info.width, info.height) or display.render_message(
            "ERR", info.width, info.height
        )
        await client.send_image_hex(_to_png(image).hex(), ".png")


async def serve(work) -> None:
    """Run `work` under SIGINT/SIGTERM, cancelling it so shutdown is orderly."""
    task = asyncio.ensure_future(work)
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        logger.info("stopping")


def _size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    return int(width), int(height)


def read_config(override: Path | None, read, default: Path, what: str) -> str | None:
    """One line of configuration, with a warning when a view wants it and it
    is not there -- an unconfigured scrape is a silent no-op otherwise."""
    value = read(override) if override is not None else read()
    if value is None:
        logger.warning(
            "no %s configuration in %s -- its views will be empty", what, override or default
        )
    return value


def _views(text: str) -> tuple[str, ...]:
    """"all", or a comma-separated selection to rotate through."""
    if text == "all":
        return VIEWS
    chosen = tuple(name.strip().lower() for name in text.split(",") if name.strip())
    unknown = [name for name in chosen if name not in VIEWS]
    if unknown or not chosen:
        raise argparse.ArgumentTypeError(
            f"unknown view {', '.join(unknown) or text!r}; pick from {', '.join(VIEWS)} or all"
        )
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="BLE MAC address (default: scan for one)")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS, help="poll seconds")
    parser.add_argument("--once", action="store_true", help="draw a single frame and exit")
    parser.add_argument("--preview", metavar="PATH", help="write the frame to a PNG instead")
    parser.add_argument(
        "--size",
        type=_size,
        default=display.DEFAULT_SIZE,
        metavar="WxH",
        help="panel size for --preview (read from the device otherwise)",
    )
    parser.add_argument(
        "--view",
        type=_views,
        default=(VIEW_QUOTA,),
        help=f"what to draw: {', '.join(VIEWS)}, all, or a comma-separated selection",
    )
    parser.add_argument(
        "--rotate", type=float, default=ROTATE_SECONDS, help="seconds per view when several"
    )
    parser.add_argument(
        "--repos",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"repository list to count today's work in (default: {repos.CONFIG_PATH},"
        " falling back to github-repos.txt beside the code)",
    )
    parser.add_argument(
        "--github-user",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"file holding the GitHub login to rank on gitranks (default: {gitranks.CONFIG_PATH})",
    )
    parser.add_argument(
        "--scholar",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"file holding the Google Scholar profile id or URL (default: {scholar.CONFIG_PATH})",
    )
    parser.add_argument(
        "--git-interval",
        type=float,
        default=repos.REFRESH_SECONDS,
        metavar="N",
        help="seconds between repository rescans",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[StatusLineHandler(sys.stdout)],
    )
    if INTERACTIVE:
        # Per-frame chatter from the BLE library would trample the status line.
        logging.getLogger("pypixelcolor").setLevel(logging.WARNING)

    # Only scan repositories if some view actually asks for them.
    scanner = None
    if any(view in display.GIT_VIEWS for view in args.view):
        listed = args.repos or repos.default_path()
        paths = repos.read_paths(listed)
        if paths:
            logger.info("watching %d repositories from %s", len(paths), listed)
        else:
            logger.warning("no repositories to watch -- git views will be empty")
        scanner = repos.Scanner(paths, args.git_interval)

    # Both scrapes are opt-in: no login file, no browser and no requests.
    ranks = None
    if any(view in display.GITRANKS_VIEWS for view in args.view):
        login = read_config(args.github_user, gitranks.read_login, gitranks.CONFIG_PATH, "gitranks")
        if login:
            logger.info("ranking %s on gitranks, refreshed daily", login)
            ranks = gitranks.ranks(login)

    cites = None
    if any(view in display.SCHOLAR_VIEWS for view in args.view):
        user = read_config(args.scholar, scholar.read_user, scholar.CONFIG_PATH, "scholar")
        if user:
            logger.info("counting citations for %s, refreshed daily", user)
            cites = scholar.scholar(user)

    panel = Panel(args.view, scanner, ranks, cites, args.interval, args.rotate)

    # A one-shot run exits before a background refresh could finish, so on a
    # cold cache it has to wait for the scrape rather than draw nothing.
    if args.once or args.preview:
        for source in (ranks, cites):
            if source is not None:
                source.warm()

    if args.preview:
        width, height = args.size
        image = panel.frame(width, height) or display.render_message("ERR", width, height)
        image.save(args.preview)
        return

    # Rotation needs a faster loop than the poll; the caches keep it cheap.
    tick = min(args.interval, args.rotate) if len(args.view) > 1 else args.interval
    try:
        asyncio.run(
            serve(run_once(args.address, panel) if args.once else run(args.address, panel, tick))
        )
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    finally:
        if INTERACTIVE:
            sys.stdout.write("\n")  # leave the status line behind on exit


if __name__ == "__main__":
    main()
