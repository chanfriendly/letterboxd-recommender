"""
Letterboxd data import helpers.

Two sources:
  1. ZIP export  — full history, uploaded once during setup
  2. RSS feed    — recent diary entries, polled on a schedule (no auth needed)
"""

import csv
import io
import zipfile
import xml.etree.ElementTree as ET

import httpx


# ---------------------------------------------------------------------------
# ZIP export parser
# ---------------------------------------------------------------------------

def parse_letterboxd_zip(zip_bytes: bytes) -> list[dict]:
    """
    Parse a Letterboxd data-export ZIP.
    Returns a unified list of {title, year, rating, lb_uri, watched_only}.
    Includes both rated films (ratings.csv) and unrated watched films (watched.csv).
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        rated: dict[str, dict] = {}
        watched_only: list[dict] = []

        if "ratings.csv" in names:
            with zf.open("ratings.csv") as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                    title = row.get("Name", "").strip()
                    if not title:
                        continue
                    uri = row.get("Letterboxd URI", "").strip()
                    year_str = row.get("Year", "")
                    rating_str = row.get("Rating", "")
                    date_str = row.get("Date", "").strip()
                    entry = {
                        "title": title,
                        "year": int(year_str) if year_str.isdigit() else None,
                        "rating": float(rating_str) if rating_str else None,
                        "lb_uri": uri,
                        "watched_only": False,
                        "watched_date": date_str or None,
                    }
                    rated[uri or title] = entry

        if "watched.csv" in names:
            with zf.open("watched.csv") as f:
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                    title = row.get("Name", "").strip()
                    if not title:
                        continue
                    uri = row.get("Letterboxd URI", "").strip()
                    key = uri or title
                    if key not in rated:
                        year_str = row.get("Year", "")
                        date_str = row.get("Date", "").strip()
                        watched_only.append({
                            "title": title,
                            "year": int(year_str) if year_str.isdigit() else None,
                            "rating": None,
                            "lb_uri": uri,
                            "watched_only": True,
                            "watched_date": date_str or None,
                        })

    return list(rated.values()) + watched_only


# ---------------------------------------------------------------------------
# RSS feed fetcher (for scheduled incremental updates)
# ---------------------------------------------------------------------------

_LB_NS = "https://letterboxd.com"
_ATOM_NS = "http://www.w3.org/2005/Atom"


def fetch_rss_entries(username: str) -> list[dict]:
    """
    Fetch recent diary entries from a public Letterboxd RSS feed.
    Returns list of {title, year, rating, watched_date}.
    Rating is None for watched-but-unrated entries.
    """
    url = f"https://letterboxd.com/{username}/rss/"
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; letterboxd-recommender/1.0)"
        })
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Could not fetch RSS for {username}: {e}")

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise RuntimeError(f"Invalid RSS from {username}: {e}")

    ns = {"lb": _LB_NS}
    entries = []
    for item in root.findall(".//item"):
        title = item.findtext(f"{{{_LB_NS}}}filmTitle")
        year_text = item.findtext(f"{{{_LB_NS}}}filmYear")
        rating_text = item.findtext(f"{{{_LB_NS}}}memberRating")
        date_text = item.findtext(f"{{{_LB_NS}}}watchedDate")

        if not title:
            continue

        # Extract film slug from the diary entry link:
        # e.g. https://letterboxd.com/user/film/the-godfather/ → the-godfather
        link = item.findtext("link") or ""
        parts = link.rstrip("/").split("/")
        slug = parts[-1] if parts else None

        entries.append({
            "title": title.strip(),
            "year": int(year_text) if year_text and year_text.isdigit() else None,
            "rating": float(rating_text) if rating_text else None,
            "watched_date": date_text,
            "watched_only": rating_text is None,
            "slug": slug,
        })

    return entries
