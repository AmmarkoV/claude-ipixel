"""Draw remaining Claude session/week quota on an iPixel LED matrix."""

import argparse
import asyncio
import io
import logging

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

    def frame(self, width: int, height: int):
        try:
            current = usage.fetch()
        except usage.AuthError as exc:
            logger.error("auth failed: %s", exc)
            self.consecutive_failures = 0
            return display.render_message("AUTH", width, height)
        except Exception as exc:
            self.consecutive_failures += 1
            logger.warning("fetch failed (%d): %s", self.consecutive_failures, exc)
            if self.consecutive_failures < FAILURES_BEFORE_ERROR:
                return None  # keep whatever is on screen
            return display.render_message("ERR", width, height)

        self.consecutive_failures = 0
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
                last_png = None
                while True:
                    image = panel.frame(info.width, info.height)
                    if image is not None:
                        png = _to_png(image)
                        if png != last_png:
                            await client.send_image_hex(png.hex(), ".png")
                            last_png = png
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

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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


if __name__ == "__main__":
    main()
