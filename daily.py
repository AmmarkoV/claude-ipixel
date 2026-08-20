"""One scraped value, refreshed at most once a day and kept on disk.

Both scrapes this serves are somebody else's server: gitranks recomputes its
board daily and scholar changes even more slowly, so asking either of them more
than once a day would spend their bandwidth on numbers that cannot have moved.
The cache file also means a restart draws the real figures immediately instead
of an empty panel.

Refreshes run on a thread. A scrape takes anywhere from a second to the better
part of a minute, and the panel redraws every few seconds, so the caller is
handed whatever is already known and picks up the new value on a later frame.
"""

import logging
import os
import threading
import time
from pathlib import Path

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "claude-ipixel"

REFRESH_SECONDS = 24 * 3600
RETRY_SECONDS = 3600  # after a failure, before bothering them again

logger = logging.getLogger("claude-ipixel")


class Daily:
    """Serves the last known value, refreshing it in the background when stale.

    `fetch` returns a fresh value or raises; `load` and `save` persist it. The
    value has to carry a `fetched_at` timestamp, which is what "stale" means.
    """

    def __init__(self, label, fetch, load, save, refresh: float = REFRESH_SECONDS):
        self.label = label
        self._fetch, self._load, self._save = fetch, load, save
        self.refresh = refresh
        self._value = load()
        self._lock = threading.Lock()
        self._busy = False
        self._failed_at: float | None = None

    def _stale(self) -> bool:
        now = time.time()
        if self._failed_at is not None and now - self._failed_at < RETRY_SECONDS:
            return False
        if self._value is None:
            return True
        return now - self._value.fetched_at >= self.refresh

    def _update(self) -> None:
        try:
            value = self._fetch()
            self._save(value)
        except Exception as exc:
            logger.warning("%s refresh failed: %s", self.label, exc)
            with self._lock:
                self._failed_at, self._busy = time.time(), False
            return
        logger.info("%s: %s", self.label, value)
        with self._lock:
            self._value, self._failed_at, self._busy = value, None, False

    def warm(self) -> None:
        """Refresh on this thread instead of a background one -- for one-shot
        runs, which exit long before a thread of their own could finish."""
        if self._stale():
            self._update()

    def value(self):
        with self._lock:
            if not self._busy and self._stale():
                self._busy = True
                threading.Thread(target=self._update, daemon=True).start()
            return self._value
