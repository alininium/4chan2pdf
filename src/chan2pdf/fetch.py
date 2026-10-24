"""HTTP fetching with a polite delay, retries and an on-disk cache.

Local paths and file:// URLs are read straight from disk, so a page saved
with the browser's "Save page as… (complete)" can be converted offline.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 4chan2pdf/0.1"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, url: str, reason: str, status: int | None = None):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.status = status


def local_path(url: str) -> Path | None:
    """Return the filesystem path for a local path or file:// URL, else None."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if not parsed.scheme or len(parsed.scheme) == 1:  # relative path, or a Windows drive "C:"
        return Path(url)
    return None


class Fetcher:
    def __init__(
        self,
        cache_dir: Path | None = None,
        delay: float = 1.0,
        user_agent: str = DEFAULT_UA,
        cookies: dict[str, str] | None = None,
        retries: int = 4,
        timeout: float = 60.0,
    ):
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.retries = retries
        self.client = httpx.Client(
            headers={"User-Agent": user_agent},
            cookies=cookies,
            follow_redirects=True,
            timeout=timeout,
        )
        self._lock = threading.Lock()
        self._last_request = 0.0

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / digest[:2] / digest

    def _wait_turn(self) -> None:
        # One request at a time per `delay` window, even with several worker threads.
        with self._lock:
            wait = self._last_request + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get(self, url: str, use_cache: bool = True) -> bytes:
        path = local_path(url)
        if path is not None:
            try:
                return path.read_bytes()
            except OSError as e:
                raise FetchError(url, str(e)) from e

        cached = self.cache_path(url)
        if use_cache and cached and cached.exists():
            return cached.read_bytes()

        last_error = "unknown error"
        status = None
        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                resp = self.client.get(url)
            except httpx.ProxyError as e:  # a proxy refusing the host will not change its mind
                raise FetchError(url, str(e) or "proxy error") from e
            except httpx.TransportError as e:
                last_error, status = str(e) or type(e).__name__, None
            else:
                if resp.status_code == 200:
                    data = resp.content
                    if cached:
                        cached.parent.mkdir(parents=True, exist_ok=True)
                        tmp = cached.with_suffix(".tmp")
                        tmp.write_bytes(data)
                        tmp.replace(cached)
                    return data
                status = resp.status_code
                last_error = f"HTTP {status}"
                if status not in RETRY_STATUS:
                    break
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit() and attempt < self.retries:
                    time.sleep(min(int(retry_after), 120))  # the server's wait replaces ours
                    continue
            if attempt < self.retries:
                backoff = 2 ** (attempt + 1)
                log.debug("retrying %s in %ss (%s)", url, backoff, last_error)
                time.sleep(backoff)
        raise FetchError(url, last_error, status)
