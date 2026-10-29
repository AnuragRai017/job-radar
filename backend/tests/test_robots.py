from __future__ import annotations

import asyncio
from typing import Any

from app.scrapers.base import HostRateLimiter, RobotsCache


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _allows(cache: RobotsCache, session: FakeSession, url: str) -> bool:
    limiter = HostRateLimiter(interval_seconds=0)
    return asyncio.run(cache.is_allowed(url, "RoleRadarBot/1.0", session, limiter))


def test_robots_404_allows_access() -> None:
    cache = RobotsCache()
    session = FakeSession(FakeResponse(404))
    assert _allows(cache, session, "https://example.com/jobs") is True


def test_robots_disallow_rule_is_enforced() -> None:
    cache = RobotsCache()
    session = FakeSession(
        FakeResponse(200, "User-agent: *\nDisallow: /private\nAllow: /private/ok\n")
    )

    assert _allows(cache, session, "https://example.com/private/role") is False
    assert _allows(cache, session, "https://example.com/private/ok") is True
    assert session.calls == 1  # Parsed robots.txt is cached per host.


def test_robots_5xx_denies_and_negative_caches() -> None:
    cache = RobotsCache()
    session = FakeSession(FakeResponse(503))

    assert _allows(cache, session, "https://example.com/jobs") is False
    assert _allows(cache, session, "https://example.com/other") is False
    assert session.calls == 1  # Do not hammer a failing robots endpoint.


def test_robots_network_error_denies() -> None:
    cache = RobotsCache()
    session = FakeSession(error=OSError("connection reset"))
    assert _allows(cache, session, "https://example.com/jobs") is False
