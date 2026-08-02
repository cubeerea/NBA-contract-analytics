"""Tests for the shared HTTP client.

The cross-process limiter exists because of a real incident: the orchestrator
running alongside three parallel scraper processes drew a Basketball-Reference
429 with Retry-After 3597 (a one-hour jail) even though every individual
process stayed under 18 req/min. These tests pin the behaviour that prevents a
repeat.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time

import pytest

from etl import config, ratelimit


class TestTokenBucket:
    def test_paces_within_process(self):
        bucket = ratelimit.TokenBucket(60)  # one per second
        start = time.monotonic()
        for _ in range(3):
            bucket.acquire()
        # First is free (burst=1), so 3 acquires cost ~2 intervals.
        assert 1.7 <= time.monotonic() - start <= 3.0

    def test_refills_continuously_not_in_windows(self):
        # A discrete-window limiter allows a double burst across the boundary,
        # which is exactly the pattern that trips bot detection.
        bucket = ratelimit.TokenBucket(600, burst=1)
        bucket.acquire()
        start = time.monotonic()
        bucket.acquire()
        assert time.monotonic() - start >= 0.08


class TestCrossProcessLimiter:
    @pytest.fixture(autouse=True)
    def _isolate_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "RATELIMIT_STATE_DIR", tmp_path / "rl")

    def test_allows_up_to_the_limit_immediately(self):
        limiter = ratelimit.CrossProcessLimiter("example.test", rate_per_minute=5)
        start = time.monotonic()
        for _ in range(5):
            limiter.acquire()
        assert time.monotonic() - start < 1.0

    def test_reports_wait_once_window_is_full(self):
        limiter = ratelimit.CrossProcessLimiter("example.test", rate_per_minute=2)
        assert limiter._try_reserve() == 0.0
        assert limiter._try_reserve() == 0.0
        # Third request in the same window must be told to wait.
        wait = limiter._try_reserve()
        assert wait > 55.0

    def test_state_persists_across_instances(self):
        # This is the whole point: a second process must see the first's usage.
        a = ratelimit.CrossProcessLimiter("example.test", rate_per_minute=2)
        a._try_reserve()
        a._try_reserve()
        b = ratelimit.CrossProcessLimiter("example.test", rate_per_minute=2)
        assert b._try_reserve() > 55.0

    def test_prunes_stale_timestamps(self):
        limiter = ratelimit.CrossProcessLimiter("example.test", rate_per_minute=2)
        limiter.path.parent.mkdir(parents=True, exist_ok=True)
        # Two requests, but from over a minute ago — they must not count.
        limiter.path.write_text(json.dumps([time.time() - 120, time.time() - 90]))
        assert limiter._try_reserve() == 0.0

    def test_corrupt_state_does_not_fail_the_build(self):
        limiter = ratelimit.CrossProcessLimiter("example.test", rate_per_minute=2)
        limiter.path.parent.mkdir(parents=True, exist_ok=True)
        limiter.path.write_text("{not valid json")
        assert limiter._try_reserve() == 0.0

    def test_acquire_gives_up_rather_than_blocking_forever(self):
        limiter = ratelimit.CrossProcessLimiter("example.test", rate_per_minute=1)
        limiter.acquire()
        start = time.monotonic()
        limiter.acquire(max_wait=0.5)  # would otherwise wait ~60s
        assert time.monotonic() - start < 2.0


def _hammer(state_dir: str, n: int, out) -> None:
    """Child process: reserve n slots and report how many were granted free."""
    from etl import config as child_config
    from etl import ratelimit as child_ratelimit

    child_config.RATELIMIT_STATE_DIR = __import__("pathlib").Path(state_dir)
    limiter = child_ratelimit.CrossProcessLimiter("shared.test", rate_per_minute=10)
    granted = sum(1 for _ in range(n) if limiter._try_reserve() == 0.0)
    out.put(granted)


class TestActualConcurrency:
    def test_separate_processes_share_one_budget(self, tmp_path):
        """The regression test for the incident.

        Four processes each try to take 10 slots against a budget of 10. If the
        limit were per-process they would collectively take 40. The file lock
        must hold the total to exactly 10.
        """
        state = tmp_path / "rl"
        state.mkdir(parents=True)
        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        procs = [
            ctx.Process(target=_hammer, args=(str(state), 10, queue))
            for _ in range(4)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)

        total = sum(queue.get() for _ in range(4))
        assert total == 10, f"expected exactly 10 grants across processes, got {total}"


class TestRetryAfterHandling:
    def test_long_retry_after_is_not_slept_off(self, monkeypatch, tmp_path):
        """A one-hour Retry-After must fail fast, not block the build.

        The original implementation called time.sleep(3597) inside a 3-attempt
        retry loop — up to three hours of a stalled build producing nothing.
        """
        monkeypatch.setattr(config, "DATA_RAW", tmp_path)
        monkeypatch.setattr(config, "RATELIMIT_STATE_DIR", tmp_path / "rl")

        class FakeResponse:
            status_code = 429
            headers = {"Retry-After": "3597"}

        slept: list[float] = []
        monkeypatch.setattr(ratelimit.time, "sleep", lambda s: slept.append(s))
        monkeypatch.setattr(
            ratelimit.requests, "get", lambda *a, **k: FakeResponse()
        )

        with pytest.raises(ratelimit.SourceUnavailable, match="rate limited"):
            ratelimit.fetch("https://www.basketball-reference.com/x.html")

        assert not any(s > config.MAX_429_BACKOFF_SECONDS for s in slept), (
            f"slept for {slept}, must never exceed "
            f"{config.MAX_429_BACKOFF_SECONDS}s on a 429"
        )

    def test_short_retry_after_is_ridden_out(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "DATA_RAW", tmp_path)
        monkeypatch.setattr(config, "RATELIMIT_STATE_DIR", tmp_path / "rl")

        calls = {"n": 0}

        class Resp:
            def __init__(self, code):
                self.status_code = code
                # No charset in Content-Type — mirrors what BBRef actually
                # sends, so this also exercises the encoding-sniff path.
                self.headers = {"Retry-After": "1", "Content-Type": "text/html"}
                self.text = "<html>ok</html>"
                self.encoding = "ISO-8859-1"
                self.apparent_encoding = "utf-8"

            def raise_for_status(self):
                pass

        def fake_get(*a, **k):
            calls["n"] += 1
            return Resp(429) if calls["n"] == 1 else Resp(200)

        monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)
        monkeypatch.setattr(ratelimit.requests, "get", fake_get)

        out = ratelimit.fetch("https://www.basketball-reference.com/y.html")
        assert "ok" in out
        assert calls["n"] == 2
