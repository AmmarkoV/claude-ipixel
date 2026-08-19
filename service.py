"""Draw remaining Claude session/week quota on an iPixel LED matrix."""

import argparse
import asyncio
import io
import logging
import sys
import time

from bleak import BleakScanner
from pypixelcolor import AsyncClient

import display
import usage

NAME_PREFIX = "LED_BLE_"  # panels advertise as LED_BLE_<last 4 bytes of the MAC>
SCAN_SECONDS = 8.0
POLL_SECONDS = 60
RECONNECT_SECONDS = 15
FAILURES_BEFORE_ERROR = 3  # ride out transient network blips on the last frame

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


class Panel:
    """Turns the usage endpoint into a frame, tolerating transient failures."""

    def __init__(self):
        self.consecutive_failures = 0
        self.status = "starting"

    def frame(self, width: int, height: int):
        try:
            current = usage.fetch()
        except usage.AuthError as exc:
            logger.error("auth failed: %s", exc)
            self.consecutive_failures = 0
            self.status = f"auth failed: {exc}"
            return display.render_message("AUTH", width, height)
        except Exception as exc:
            self.consecutive_failures += 1
            logger.warning("fetch failed (%d): %s", self.consecutive_failures, exc)
            self.status = f"fetch failed ({self.consecutive_failures}): {exc}"
            if self.consecutive_failures < FAILURES_BEFORE_ERROR:
                return None  # keep whatever is on screen
            return display.render_message("ERR", width, height)

        self.consecutive_failures = 0
        self.status = _quota_status(current)
        return display.render(current, width, height)


async def run(address: str | None, interval: int) -> None:
    panel = Panel()
    while True:
        try:
            # Rediscover on every reconnect: these panels use a random BLE
            # address, which changes when the device power-cycles.
            target = address or await discover_address()
            async with AsyncClient(target) as client:
                info = client.get_device_info()
                logger.info("connected to %s (%dx%d)", target, info.width, info.height)
                last_png, sent_at = None, None
                while True:
                    image = panel.frame(info.width, info.height)
                    if image is not None:
                        png = _to_png(image)
                        if png != last_png:
                            await client.send_image_hex(png.hex(), ".png")
                            last_png, sent_at = png, time.time()
                    _write_status(panel.status, sent_at)
                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("device error, retrying: %s", exc)
            await asyncio.sleep(RECONNECT_SECONDS)


async def run_once(address: str | None) -> None:
    target = address or await discover_address()
    async with AsyncClient(target) as client:
        info = client.get_device_info()
        image = Panel().frame(info.width, info.height) or display.render_message(
            "ERR", info.width, info.height
        )
        await client.send_image_hex(_to_png(image).hex(), ".png")


def _size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    return int(width), int(height)


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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[StatusLineHandler(sys.stdout)],
    )
    if INTERACTIVE:
        # Per-frame chatter from the BLE library would trample the status line.
        logging.getLogger("pypixelcolor").setLevel(logging.WARNING)

    if args.preview:
        width, height = args.size
        image = Panel().frame(width, height) or display.render_message("ERR", width, height)
        image.save(args.preview)
        return

    try:
        asyncio.run(run_once(args.address) if args.once else run(args.address, args.interval))
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)
    finally:
        if INTERACTIVE:
            sys.stdout.write("\n")  # leave the status line behind on exit


if __name__ == "__main__":
    main()
