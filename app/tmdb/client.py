"""
TMDB API client (async).
Docs: https://developer.themoviedb.org/docs
"""

import httpx
from app.config import settings

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBClient:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=TMDB_BASE,
            params={"api_key": settings.tmdb_api_key},
            timeout=15,
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def get_movie(self, tmdb_id: int) -> dict | None:
        """Fetch movie details including genres."""
        try:
            resp = await self._client.get(f"/movie/{tmdb_id}")
            resp.raise_for_status()
            data = resp.json()
            return {
                "tmdb_id": tmdb_id,
                "title": data.get("title", ""),
                "year": (data.get("release_date") or "")[:4] or None,
                "overview": data.get("overview", ""),
                "tmdb_rating": data.get("vote_average"),
                "poster_url": (
                    f"{POSTER_BASE}{data['poster_path']}"
                    if data.get("poster_path")
                    else None
                ),
                "genres": [
                    {"tmdb_genre_id": g["id"], "name": g["name"]}
                    for g in data.get("genres", [])
                ],
            }
        except (httpx.HTTPError, KeyError):
            return None

    async def search_movie(self, title: str, year: int | None = None) -> int | None:
        """Fallback: find a TMDB ID by title + year."""
        params = {"query": title}
        if year:
            params["year"] = year
        try:
            resp = await self._client.get("/search/movie", params=params)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                return results[0]["id"]
        except httpx.HTTPError:
            pass
        return None

    async def get_genres(self) -> list[dict]:
        """Return full TMDB genre list."""
        resp = await self._client.get("/genre/movie/list")
        resp.raise_for_status()
        return resp.json().get("genres", [])
