from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from protego import Protego

from app.config import settings

logger = logging.getLogger(__name__)

DESKTOP_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]

IOS_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]


@dataclass(frozen=True)
class BrowserProfile:
    name: str
    impersonate: str
    user_agents: tuple[str, ...]


PROFILES: dict[str, BrowserProfile] = {
    "desktop": BrowserProfile("desktop", "chrome", tuple(DESKTOP_AGENTS)),
    "avature": BrowserProfile("avature", "safari17_2_ios", tuple(IOS_AGENTS)),
}


class FetchError(RuntimeError):
    def __init__(self, url: str, message: str, status_code: int | None = None):
        self.url = url
        self.status_code = status_code
        self.message = message
        super().__init__(f"{message} (url={url}, status={status_code})")


class RobotsDisallowed(FetchError):
    pass


@dataclass
class HttpResponse:
    url: str
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> object:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise FetchError(self.url, f"Invalid JSON response: {exc}", self.status_code) from exc


class HostRateLimiter:
    """Serialises requests per host and keeps a minimum gap between them."""

    def __init__(self, interval_seconds: float | None = None):
        self.interval = interval_seconds if interval_seconds is not None else settings.http_rate_limit_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._guard = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self._guard:
            lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last_request.get(host, 0.0)
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self._last_request[host] = time.monotonic()


class RobotsCache:
    """Caches robots.txt per scheme+host and answers can_fetch().

    RFC 9309 treats 4xx as "no restrictions known", but a 5xx response or an
    unreachable robots file means restrictions could not be checked. Those
    failures are denied (and retried after a short negative-cache window)
    rather than silently allowing every URL.
    """

    FAILURE_CACHE_SECONDS = 300.0

    def __init__(self) -> None:
        self._parsers: dict[str, Protego | None] = {}
        self._fetched_at: dict[str, float] = {}
        self._denied_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(key, asyncio.Lock())

    async def is_allowed(
        self,
        url: str,
        user_agent: str,
        session: AsyncSession,
        limiter: HostRateLimiter,
    ) -> bool:
        parsed = urlparse(url)
        key = f"{parsed.scheme}://{parsed.netloc}"
        lock = await self._lock_for(key)

        async with lock:
            now = time.monotonic()
            denied = self._denied_at.get(key)
            if denied is not None and (now - denied) < self.FAILURE_CACHE_SECONDS:
                return False
            if (
                key in self._parsers
                and (now - self._fetched_at.get(key, 0.0)) < settings.robots_cache_seconds
            ):
                parser = self._parsers[key]
                return parser.can_fetch(url, user_agent) if parser else True

            robots_url = f"{key}/robots.txt"
            parser: Protego | None = None
            failed = False
            try:
                await limiter.wait(parsed.netloc)
                response = await session.get(
                    robots_url,
                    headers={"User-Agent": user_agent, "Accept": "text/plain,*/*"},
                    timeout=min(15, settings.http_timeout_seconds),
                    allow_redirects=True,
                )
                if response.status_code in (401, 403, 404, 410):
                    # RFC 9309: a 4xx response means no restrictions are known.
                    # Some Oracle hosts return 403 for robots.txt itself.
                    logger.debug(
                        "robots.txt returned %s for %s; treating as allow",
                        response.status_code,
                        key,
                    )
                elif 200 <= response.status_code < 300:
                    parser = Protego.parse(response.text)
                else:
                    failed = True
                    logger.warning(
                        "robots.txt returned %s for %s; denying requests until the next check",
                        response.status_code,
                        key,
                    )
            except (TimeoutError, RequestException, OSError) as exc:
                failed = True
                logger.warning(
                    "Could not read robots.txt for %s (%s); denying requests until the next check",
                    key,
                    exc,
                )

            if failed:
                self._denied_at[key] = time.monotonic()
                self._parsers.pop(key, None)
                self._fetched_at.pop(key, None)
                return False

            self._denied_at.pop(key, None)
            self._parsers[key] = parser
            self._fetched_at[key] = time.monotonic()

        return parser.can_fetch(url, user_agent) if parser else True


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
        return max(0.0, target.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


class HttpClient:
    """curl_cffi-backed async client with TLS impersonation, retries, robots
    enforcement, per-host rate limiting, and User-Agent rotation."""

    RETRYABLE_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}

    def __init__(self, profile: str = "desktop", rate_limiter: HostRateLimiter | None = None):
        self.profile = PROFILES.get(profile, PROFILES["desktop"])
        self.limiter = rate_limiter or HostRateLimiter()
        self.robots = RobotsCache()
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> HttpClient:
        self._session = AsyncSession(
            impersonate=self.profile.impersonate,
            timeout=settings.http_timeout_seconds,
            verify=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("HttpClient must be used as an async context manager")
        return self._session

    def _headers(self, *, expect_json: bool, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": random.choice(self.profile.user_agents),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": (
                "application/json, text/plain, */*"
                if expect_json
                else "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if extra:
            headers.update(extra)
        return headers

    async def can_fetch(self, url: str) -> bool:
        if not settings.respect_robots:
            return True
        ua = self.profile.user_agents[0]
        return await self.robots.is_allowed(url, ua, self.session, self.limiter)

    async def get(
        self,
        url: str,
        *,
        expect_json: bool = False,
        enforce_robots: bool | None = None,
        headers: dict[str, str] | None = None,
        fallback_html: bool = False,
    ) -> HttpResponse:
        return await self._request(
            "GET",
            url,
            expect_json=expect_json,
            enforce_robots=enforce_robots,
            headers=headers,
            fallback_html=fallback_html,
        )

    async def post(
        self,
        url: str,
        *,
        json: object | None = None,
        expect_json: bool = True,
        enforce_robots: bool | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        return await self._request(
            "POST",
            url,
            expect_json=expect_json,
            enforce_robots=enforce_robots,
            headers=headers,
            json_body=json,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        expect_json: bool = False,
        enforce_robots: bool | None = None,
        headers: dict[str, str] | None = None,
        fallback_html: bool = False,
        json_body: object | None = None,
    ) -> HttpResponse:
        if enforce_robots is None:
            enforce_robots = settings.respect_robots
        request_headers = self._headers(expect_json=expect_json, extra=headers)
        if json_body is not None:
            request_headers.setdefault("Content-Type", "application/json")

        if enforce_robots:
            allowed = await self.robots.is_allowed(
                url, request_headers["User-Agent"], self.session, self.limiter
            )
            if not allowed:
                raise RobotsDisallowed(url, "Blocked by robots.txt")

        host = urlparse(url).netloc
        last_error: Exception | None = None
        attempts = settings.http_max_retries + 1

        for attempt in range(attempts):
            if attempt:
                backoff = min(
                    settings.http_backoff_max_seconds,
                    settings.http_backoff_base_seconds * (2 ** (attempt - 1)),
                )
                await asyncio.sleep(backoff + random.uniform(0, 0.8))

            await self.limiter.wait(host)
            started = time.monotonic()
            try:
                response = await self.session.request(
                    method,
                    url,
                    headers=request_headers,
                    json=json_body,
                    impersonate=self.profile.impersonate,
                    timeout=settings.http_timeout_seconds,
                    allow_redirects=True,
                )
            except (TimeoutError, RequestException, OSError) as exc:
                last_error = exc
                logger.warning(
                    "HTTP error %s %s (attempt %d/%d): %s",
                    self.profile.name,
                    url,
                    attempt + 1,
                    attempts,
                    exc,
                )
                continue

            duration_ms = int((time.monotonic() - started) * 1000)
            logger.debug("HTTP %s %s -> %s (%dms)", self.profile.name, url, response.status_code, duration_ms)

            if response.status_code in self.RETRYABLE_STATUS and attempt < attempts - 1:
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if retry_after:
                    await asyncio.sleep(min(retry_after, settings.http_backoff_max_seconds))
                logger.warning(
                    "Retryable HTTP %s for %s (attempt %d/%d)",
                    response.status_code,
                    url,
                    attempt + 1,
                    attempts,
                )
                continue

            if response.status_code >= 400:
                if fallback_html and settings.enable_driverless_fallback and self._session is not None:
                    logger.warning("Trying Selenium-Driverless fallback for %s", url)
                    try:
                        html = await asyncio.to_thread(
                            fetch_with_driverless,
                            url,
                            settings.driverless_headless,
                            settings.http_timeout_seconds,
                        )
                        if html:
                            return HttpResponse(str(response.url or url), 200, html, dict(response.headers))
                    except Exception as fallback_exc:  # noqa: BLE001
                        logger.warning("Driverless fallback failed for %s: %s", url, fallback_exc)
                raise FetchError(url, "HTTP request failed", response.status_code)

            return HttpResponse(
                str(response.url or url),
                response.status_code,
                response.text,
                dict(response.headers),
            )

        raise FetchError(url, f"Request failed after {attempts} attempts: {last_error}")


def fetch_with_driverless(url: str, headless: bool = True, timeout: float = 60.0) -> str:
    """Optional last-resort HTML fetch. Imported lazily so the default image
    stays small. Requires requirements-browser.txt and Chrome installed."""
    try:
        from selenium_driverless.sync import webdriver  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "selenium-driverless is not installed; install requirements-browser.txt"
        ) from exc

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    with webdriver.Chrome(options=options) as driver:  # pragma: no cover - optional
        driver.get(url)
        time.sleep(min(timeout, 60))
        return driver.page_source
