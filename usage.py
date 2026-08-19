"""Fetch Claude usage limits from the OAuth usage endpoint.

Credentials are read fresh from ~/.claude/.credentials.json on every call and
never written back, so this stays out of the way of Claude Code's own token
refresh. If the access token expires before Claude Code refreshes it, the
endpoint returns 401 and we raise AuthError.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


class AuthError(Exception):
    """Credentials are missing, malformed or rejected."""


@dataclass
class Limit:
    utilization: float  # percent used, 0-100+
    resets_at: datetime | None

    @property
    def remaining(self) -> float:
        return max(0.0, 100.0 - self.utilization)

    @property
    def exhausted(self) -> bool:
        return self.utilization >= 100.0

    def seconds_until_reset(self, now: datetime | None = None) -> float | None:
        if self.resets_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0.0, (self.resets_at - now).total_seconds())


@dataclass
class Usage:
    session: Limit  # rolling 5 hour window
    week: Limit  # rolling 7 day window


def _read_token() -> str:
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except FileNotFoundError:
        raise AuthError(f"no credentials at {CREDENTIALS_PATH}")
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"unreadable credentials: {exc}")

    token = data.get("claudeAiOauth", {}).get("accessToken")
    if not token:
        raise AuthError("no claudeAiOauth.accessToken in credentials")
    return token


def _parse_limit(payload: dict | None) -> Limit:
    if not payload:
        return Limit(utilization=0.0, resets_at=None)
    resets_at = payload.get("resets_at")
    return Limit(
        utilization=float(payload.get("utilization") or 0.0),
        resets_at=datetime.fromisoformat(resets_at) if resets_at else None,
    )


def fetch(timeout: float = 15.0) -> Usage:
    """Fetch current usage. Raises AuthError or OSError/urllib errors."""
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {_read_token()}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError(f"usage endpoint returned {exc.code}")
        raise

    return Usage(
        session=_parse_limit(body.get("five_hour")),
        week=_parse_limit(body.get("seven_day")),
    )
