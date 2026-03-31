import asyncio
import time
from app.config import settings


class RateLimiter:
    """Simple token bucket rate limiter for async HTTP requests."""

    def __init__(self, rps: float | None = None):
        self.min_interval = 1.0 / (rps or settings.lb_rate_limit_rps)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


# Shared limiter instance for all Letterboxd requests
letterboxd_limiter = RateLimiter()
