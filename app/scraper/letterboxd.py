"""
Letterboxd scraping client.
All HTTP calls go through the rate limiter.
"""

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.scraper.rate_limiter import letterboxd_limiter
from app.scraper.parsers import (
    parse_user_films_page,
    parse_film_page,
    parse_film_members_page,
    get_total_pages,
)

BASE_URL = "https://letterboxd.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class LetterboxdScraper:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _get(self, url: str) -> str:
        await letterboxd_limiter.acquire()
        response = await self._client.get(url)
        response.raise_for_status()
        return response.text

    async def get_user_ratings(self, username: str) -> list[dict]:
        """
        Fetch all rated films for a user.
        Returns list of {slug, rating}.
        """
        all_films = []
        page = 1

        # First page to determine total pages
        url = f"{BASE_URL}/{username}/films/ratings/page/1/"
        html = await self._get(url)
        total_pages = get_total_pages(html)
        all_films.extend(parse_user_films_page(html))

        for page in range(2, total_pages + 1):
            url = f"{BASE_URL}/{username}/films/ratings/page/{page}/"
            html = await self._get(url)
            all_films.extend(parse_user_films_page(html))

        return all_films

    async def get_film_details(self, slug: str) -> dict:
        """
        Fetch metadata from a film's detail page.
        Returns {tmdb_id, title, year, lb_rating}.
        """
        url = f"{BASE_URL}/film/{slug}/"
        html = await self._get(url)
        return parse_film_page(html)

    async def get_film_audience_ratings(
        self, slug: str, max_pages: int = 5
    ) -> list[dict]:
        """
        Fetch ratings from other users for a specific film.
        Returns list of {username, rating}.
        """
        all_members = []

        for page in range(1, max_pages + 1):
            url = f"{BASE_URL}/film/{slug}/members/page/{page}/"
            try:
                html = await self._get(url)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    break
                raise
            members = parse_film_members_page(html)
            if not members:
                break
            all_members.extend(members)

        return all_members

    async def user_exists(self, username: str) -> bool:
        """Check if a Letterboxd username exists."""
        try:
            await self._get(f"{BASE_URL}/{username}/")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise
