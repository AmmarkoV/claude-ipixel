"""Remaining DeepSeek API balance for the key in deepseek.txt.

The key is read fresh from the config directory on every call and never
logged or written anywhere else. With no deepseek.txt there is no key and
nothing is fetched.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from repos import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "deepseek.txt"
LOCAL_PATH = Path(__file__).with_name("deepseek.txt")
EXAMPLE_PATH = Path(__file__).with_name("deepseek.txt.example")

BALANCE_URL = "https://api.deepseek.com/user/balance"
TIMEOUT = 15.0
REFRESH_SECONDS = 600  # a balance barely moves; ten minutes is plenty

logger = logging.getLogger("claude-ipixel")


class AuthError(Exception):
    """The API key is rejected."""


@dataclass
class Balance:
    currency: str = ""  # "CNY"
    total: float = 0.0  # everything left to spend
    available: bool = True  # False when the balance cannot pay for an API call

    def __str__(self) -> str:
        return f"{self.total:,.2f} {self.currency}"


def read_key(path: Path | None = None) -> str | None:
    """The DeepSeek API key, or None -- which switches the feature off."""
    for candidate in (CONFIG_PATH, LOCAL_PATH) if path is None else (path,):
        try:
            lines = candidate.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.split("#", 1)[0].strip()
            if line:
                return line
    return None


def fetch(timeout: float = TIMEOUT) -> Balance:
    """Fetch the current balance. Raises AuthError or OSError/urllib errors."""
    request = urllib.request.Request(
        BALANCE_URL,
        headers={"Authorization": f"Bearer {read_key()}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError(f"balance endpoint returned {exc.code}")
        raise

    info = (body.get("balance_infos") or [{}])[0]
    return Balance(
        currency=info.get("currency") or "",
        total=float(info.get("total_balance") or 0.0),
        available=bool(body.get("is_available")),
    )


def summary(balance: Balance | None) -> str:
    if balance is None:
        return "deepseek --"
    return f"deepseek {balance}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not read_key():
        raise SystemExit(f"no API key: mkdir -p {CONFIG_DIR} && echo sk-YOURKEY > {CONFIG_PATH}")
    balance = fetch()
    print(balance, "(spent)" if not balance.available else "")
