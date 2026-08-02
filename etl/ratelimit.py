"""Shared HTTP client: token-bucket rate limiting, on-disk cache, retry.

Sports-Reference blocks clients exceeding 20 requests/minute and jails the
offending session for up to a day (https://www.sports-reference.com/bot-traffic.html).
A day-long ban would silently break the nightly build, so the limiter is
conservative by default and shared across all callers hitting the same host.

The on-disk cache exists so that iterating on a parser does not require
re-fetching. During development this is the difference between a two-minute
edit loop and a two-minute-plus-ban edit loop.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import config

log = logging.getLogger(__name__)


class TokenBucket:
    """Thread-safe token bucket.

    Refills continuously rather than in discrete windows, so a burst at the
    boundary of two windows cannot exceed the intended rate — which is exactly
    the pattern that trips Sports-Reference's detector.
    """

    def __init__(self, rate_per_minute: float, burst: int = 1):
        self.interval = 60.0 / rate_per_minute
        self.burst = max(1, burst)
        self._tokens = float(self.burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(
                    self.burst, self._tokens + elapsed / self.interval
                )
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) * self.interval
            time.sleep(deficit)


# One bucket per host. Two scrapers hitting basketball-reference.com must share
# a budget, otherwise the combined rate silently doubles.
_BUCKETS: dict[str, TokenBucket] = {}
_BUCKETS_LOCK = threading.Lock()

_HOST_LIMITS = {
    "basketball-reference.com": config.SPORTS_REFERENCE_MAX_RPM,
    "www.basketball-reference.com": config.SPORTS_REFERENCE_MAX_RPM,
    "spotrac.com": config.SPOTRAC_MAX_RPM,
    "www.spotrac.com": config.SPOTRAC_MAX_RPM,
}
_DEFAULT_RPM = 30


def _host_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def _rpm_for(host: str) -> int:
    return _HOST_LIMITS.get(host, _DEFAULT_RPM)


def _bucket_for(url: str) -> TokenBucket:
    host = _host_of(url)
    with _BUCKETS_LOCK:
        if host not in _BUCKETS:
            _BUCKETS[host] = TokenBucket(_rpm_for(host))
            log.debug("rate limiter for %s: %s req/min", host, _rpm_for(host))
        return _BUCKETS[host]


class CrossProcessLimiter:
    """Sliding-window rate limit shared by every process on this machine.

    The in-process TokenBucket above is per-interpreter, so N concurrent
    Python processes each politely pace themselves to the full budget and the
    server sees N times the intended rate. That is not hypothetical: running
    the orchestrator alongside three parallel scraper processes produced a
    Basketball-Reference 429 with Retry-After 3597 — a one-hour jail — despite
    every individual process staying under 18 req/min.

    Implemented as a flock-guarded JSON file of recent request timestamps.
    Coarse and slightly pessimistic, which is the correct bias when the
    downside is a day-long ban.
    """

    def __init__(self, host: str, rate_per_minute: int):
        self.host = host
        self.rpm = rate_per_minute
        self.path = config.RATELIMIT_STATE_DIR / f"{host}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def acquire(self, max_wait: float = 300.0) -> None:
        deadline = time.monotonic() + max_wait
        while True:
            wait = self._try_reserve()
            if wait <= 0:
                return
            if time.monotonic() + wait > deadline:
                log.warning(
                    "cross-process limiter for %s would wait %.0fs (>%.0fs cap); "
                    "proceeding — reduce concurrency if this recurs",
                    self.host,
                    wait,
                    max_wait,
                )
                return
            log.debug("cross-process limiter: waiting %.1fs for %s", wait, self.host)
            time.sleep(min(wait, 5.0))

    def _try_reserve(self) -> float:
        """Reserve a slot. Returns 0 on success, else seconds to wait."""
        now = time.time()
        with open(self.path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read().strip()
                try:
                    stamps = [float(t) for t in json.loads(raw)] if raw else []
                except (ValueError, TypeError):
                    stamps = []  # corrupt state is not worth failing a build over

                stamps = [t for t in stamps if now - t < 60.0]

                if len(stamps) < self.rpm:
                    stamps.append(now)
                    fh.seek(0)
                    fh.truncate()
                    fh.write(json.dumps(stamps))
                    return 0.0

                # Window is full; wait until the oldest request ages out.
                return max(0.0, 60.0 - (now - min(stamps))) + 0.05
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


_CP_LIMITERS: dict[str, CrossProcessLimiter] = {}


def _cross_process_limiter_for(host: str) -> CrossProcessLimiter:
    with _BUCKETS_LOCK:
        if host not in _CP_LIMITERS:
            _CP_LIMITERS[host] = CrossProcessLimiter(host, _rpm_for(host))
        return _CP_LIMITERS[host]


class SourceUnavailable(RuntimeError):
    """A source could not be fetched after retries.

    Callers decide whether this is fatal. Essential sources (BBRef stats,
    BBRef contracts) propagate it; enrichment sources (Spotrac) catch it and
    degrade. See README "Fail-soft ingestion".
    """


# Bump when a change to fetch() alters the CONTENT of what gets cached, so that
# stale entries written by the old logic are invalidated rather than silently
# reused. v2: fixed the ISO-8859-1 mojibake described in fetch().
CACHE_VERSION = 2


def _cache_path(url: str, namespace: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return config.DATA_RAW / f"v{CACHE_VERSION}" / namespace / f"{digest}.html"


def _cache_is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours < config.RAW_CACHE_TTL_HOURS


def fetch(
    url: str,
    *,
    namespace: str = "misc",
    use_cache: bool = True,
    timeout: int | None = None,
) -> str:
    """Fetch a URL as text, respecting rate limits and the on-disk cache.

    Raises SourceUnavailable if all retries fail.
    """
    path = _cache_path(url, namespace)

    if use_cache and _cache_is_fresh(path):
        log.debug("cache hit %s", url)
        # newline="" disables universal-newline translation so a cached read is
        # byte-identical to the live fetch. Without it, CRLF in the source
        # collapses to LF on read and cached and live paths diverge — harmless
        # for HTML parsing, but it makes "did the cache round-trip?" untestable.
        return path.read_text(encoding="utf-8", newline="")

    timeout = timeout or config.HTTP_TIMEOUT
    bucket = _bucket_for(url)
    cross_limiter = _cross_process_limiter_for(_host_of(url))
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    last_error: Exception | None = None
    for attempt in range(1, config.HTTP_RETRIES + 1):
        cross_limiter.acquire()
        bucket.acquire()
        try:
            log.debug("GET %s (attempt %d)", url, attempt)
            resp = requests.get(url, headers=headers, timeout=timeout)

            # 429 means we have already lost. Sports-Reference sets Retry-After
            # to the remaining jail time, which is up to an hour — sleeping it
            # off inside a retry loop would block for hours and, with 3
            # retries, could stall a build for most of a day while producing
            # nothing. Short waits are worth riding out; long ones mean we are
            # banned and should surface that immediately so the caller can fall
            # back to cache or fail loudly.
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                if wait > config.MAX_429_BACKOFF_SECONDS:
                    log.error(
                        "429 from %s with Retry-After %ss — rate limited, not "
                        "waiting it out. Reduce concurrency and retry later.",
                        url,
                        wait,
                    )
                    last_error = SourceUnavailable(
                        f"rate limited by {_host_of(url)} for {wait}s: {url}"
                    )
                    break
                log.warning("429 from %s, backing off %ss", url, wait)
                time.sleep(wait)
                last_error = SourceUnavailable(f"429 Too Many Requests: {url}")
                continue

            resp.raise_for_status()

            # Basketball-Reference serves UTF-8 but sends a bare
            # "Content-Type: text/html" with no charset parameter. Per RFC 2616
            # requests then falls back to ISO-8859-1, which silently mojibakes
            # every accented name: "Alperen Şengün" decodes as
            # "Alperen Å\x9eengÃ¼n". Because the corruption is in `resp.text`,
            # it gets baked into the on-disk cache too, so every downstream
            # name match fails on exactly the players hardest to match anyway.
            # Trust the content sniff over the (absent) header.
            if resp.encoding is None or "charset" not in (
                resp.headers.get("Content-Type", "").lower()
            ):
                detected = resp.apparent_encoding
                if detected:
                    resp.encoding = detected

            text = resp.text

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
            return text

        except requests.RequestException as exc:
            last_error = exc
            backoff = 2**attempt
            log.warning(
                "fetch failed (%s), retrying in %ss: %s", exc, backoff, url
            )
            time.sleep(backoff)

    # Exhausted retries. A stale cache entry beats no data at all for a nightly
    # build, so fall back to it if one exists — but say so loudly.
    if path.exists():
        log.error("all retries failed for %s; using STALE cache", url)
        return path.read_text(encoding="utf-8", newline="")

    raise SourceUnavailable(f"could not fetch {url}: {last_error}") from last_error
