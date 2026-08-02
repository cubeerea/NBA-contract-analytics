"""Mirror player headshots locally so WebGL can texture them.

WHY THIS EXISTS: cdn.nba.com serves headshots without an
`Access-Control-Allow-Origin` header. A plain <img> tag does not care, but
WebGL refuses to upload a cross-origin image as a texture, so deck.gl's
IconLayer cannot build its atlas directly from the CDN. Headshots ARE the data
points in this dashboard (ADR-007), so without a same-origin copy the entire
chart degrades to silhouettes.

Mirroring into data/processed/headshots/ makes them same-origin for the static
site and takes the CDN out of the render path entirely.

SIZE CHOICE: we mirror the 260x190 variant, not 1040x760. At 14.5KB versus
187KB that is a 13x reduction — roughly 7MB for the league instead of 94MB —
which matters because these are committed to git on a nightly cadence. 260x190
is comfortably more resolution than a face-sized scatter icon consumes. The
player detail card can point at the full-size CDN URL directly, since an <img>
tag has no CORS constraint.

INCREMENTAL: a player's portrait changes at most once a season, so anything
already on disk is left alone. A steady-state nightly run fetches only players
who newly entered the league, which keeps both the runtime and the git delta
near zero.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import requests

from .. import config

log = logging.getLogger(__name__)

# The mirrored variant. Deliberately not the 1040x760 used for the detail card.
MIRROR_URL = "https://cdn.nba.com/headshots/nba/latest/260x190/{player_id}.png"

# Path the frontend requests, relative to the site root.
PUBLIC_PATH = "./data/headshots/{player_id}.png"

# A CDN tolerates far more than a scraped site; this is politeness, not a limit
# imposed by the host. 8/sec clears a full league in about a minute on a cold
# start and is irrelevant on a warm one.
REQUESTS_PER_SECOND = 8.0

# NBA.com serves a generic silhouette with HTTP 200 — not a 404 — for any
# player it has no portrait for. Storing those would put anonymous outlines on
# the chart that look like real data points.
#
# A size threshold is the obvious detector and it is not good enough: the
# silhouette is 4,937 bytes while genuine portraits observed run 14-17KB, so
# any cutoff is an arbitrary line through a distribution we do not control.
# Instead we identify the placeholder EXACTLY, by hashing it. The bytes are
# identical for every missing player, so one probe of a deliberately impossible
# ID teaches us the hash, and the check becomes exact rather than heuristic —
# and it self-corrects if NBA ever changes the image.
IMPOSSIBLE_PLAYER_ID = 999_999_999

# Retained only as a cheap guard against truncated or empty responses.
MIN_PLAUSIBLE_BYTES = 1_000

_placeholder_digests: set[str] = set()


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _learn_placeholder(session: requests.Session, timeout: int) -> None:
    """Probe an impossible player ID to learn the placeholder's hash."""
    if _placeholder_digests:
        return
    try:
        resp = session.get(
            MIRROR_URL.format(player_id=IMPOSSIBLE_PLAYER_ID), timeout=timeout
        )
        if resp.status_code == 200 and resp.content:
            _placeholder_digests.add(_digest(resp.content))
            log.debug(
                "learned placeholder headshot: %d bytes", len(resp.content)
            )
    except requests.RequestException:
        # Not fatal — we simply lose exact detection and fall back to the
        # size guard for this run.
        log.debug("could not probe placeholder headshot")


def headshot_dir() -> Path:
    return config.DATA_PROCESSED / "headshots"


def mirror_headshots(
    player_ids: list[int],
    *,
    force_refresh: bool = False,
    timeout: int = 20,
) -> dict[int, str]:
    """Download missing headshots. Returns {player_id: public path}.

    Never raises on an individual failure — a missing portrait is a cosmetic
    degradation, not a reason to fail a nightly build. Players whose portrait
    could not be fetched are simply absent from the returned mapping, and the
    frontend falls back to a placeholder.
    """
    out_dir = headshot_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    resolved: dict[int, str] = {}
    to_fetch: list[int] = []

    for pid in player_ids:
        if pid is None:
            continue
        pid = int(pid)
        path = out_dir / f"{pid}.png"
        if path.exists() and path.stat().st_size >= MIN_PLAUSIBLE_BYTES:
            if force_refresh:
                to_fetch.append(pid)
            else:
                resolved[pid] = PUBLIC_PATH.format(player_id=pid)
        else:
            to_fetch.append(pid)

    if not to_fetch:
        log.info("headshots: all %d already mirrored", len(resolved))
        return resolved

    log.info(
        "headshots: %d cached, fetching %d", len(resolved), len(to_fetch)
    )

    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT
    interval = 1.0 / REQUESTS_PER_SECOND
    _learn_placeholder(session, timeout)

    failed: list[int] = []
    for i, pid in enumerate(to_fetch):
        if i:
            time.sleep(interval)
        url = MIRROR_URL.format(player_id=pid)
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code != 200:
                failed.append(pid)
                continue
            body = resp.content
            if len(body) < MIN_PLAUSIBLE_BYTES or _digest(body) in _placeholder_digests:
                # Generic silhouette served as a 200, or a truncated response.
                # Treat as missing so an anonymous outline does not masquerade
                # as a real portrait on the chart.
                failed.append(pid)
                continue
            (out_dir / f"{pid}.png").write_bytes(body)
            resolved[pid] = PUBLIC_PATH.format(player_id=pid)
        except requests.RequestException as exc:
            log.debug("headshot fetch failed for %s: %s", pid, exc)
            failed.append(pid)

    if failed:
        log.warning(
            "headshots: %d unavailable (frontend will use placeholders): %s",
            len(failed),
            failed[:10],
        )

    total_bytes = sum(p.stat().st_size for p in out_dir.glob("*.png"))
    log.info(
        "headshots: %d mirrored, %.1f MB on disk",
        len(resolved),
        total_bytes / 1e6,
    )
    return resolved
