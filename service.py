"""Draw remaining Claude session/week quota on an iPixel LED matrix."""

import argparse
import asyncio
import io
import logging

from pypixelcolor import AsyncClient

import display
import usage

DEFAULT_ADDRESS = "5B:18:0C:7E:39:FB"
POLL_SECONDS = 60
RECONNECT_SECONDS = 15
FAILURES_BEFORE_ERROR = 3  # ride out transient network blips on the last frame

logger = logging.getLogger("claude-ipixel")


def _to_png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class Panel:
    """Turns the usage endpoint into a frame, tolerating transient failures."""

    def __init__(self):
        self.consecutive_failures = 0

    def frame(self):
        try:
            current = usage.fetch()
        except usage.AuthError as exc:
            logger.error("auth failed: %s", exc)
            self.consecutive_failures = 0
            return display.render_message("AUTH")
        except Exception as exc:
            self.consecutive_failures += 1
            logger.warning("fetch failed (%d): %s", self.consecutive_failures, exc)
            if self.consecutive_failures < FAILURES_BEFORE_ERROR:
                return None  # keep whatever is on screen
            return display.render_message("ERR")

        self.consecutive_failures = 0
        return display.render(current)


async def run(address: str, interval: int) -> None:
    panel = Panel()
    while True:
        try:
            async with AsyncClient(address) as client:
                logger.info("connected to %s", address)
                last_png = None
                while True:
                    image = panel.frame()
                    if image is not None:
                        png = _to_png(image)
                        if png != last_png:
                            await client.send_image_hex(png.hex(), ".png")
                            last_png = png
                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("device error, reconnecting: %s", exc)
            await asyncio.sleep(RECONNECT_SECONDS)


async def run_once(address: str) -> None:
    image = Panel().frame() or display.render_message("ERR")
    async with AsyncClient(address) as client:
        await client.send_image_hex(_to_png(image).hex(), ".png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="BLE MAC address")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS, help="poll seconds")
    parser.add_argument("--once", action="store_true", help="draw a single frame and exit")
    parser.add_argument("--preview", metavar="PATH", help="write the frame to a PNG instead")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.preview:
        (Panel().frame() or display.render_message("ERR")).save(args.preview)
        return

    try:
        asyncio.run(run_once(args.address) if args.once else run(args.address, args.interval))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
