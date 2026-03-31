"""
HTML parsing functions for Letterboxd pages.
Isolated here so scraping logic stays clean and tests can use fixture HTML.
"""

import re
from bs4 import BeautifulSoup


def parse_film_page(html: str) -> dict:
    """
    Parse a film detail page (letterboxd.com/film/{slug}/).
    Returns: {tmdb_id, title, year, lb_rating}
    """
    soup = BeautifulSoup(html, "lxml")
    result = {}

    # TMDB link is the most reliable ID mapping
    tmdb_link = soup.find("a", href=re.compile(r"themoviedb\.org/movie/(\d+)"))
    if tmdb_link:
        match = re.search(r"/movie/(\d+)", tmdb_link["href"])
        if match:
            result["tmdb_id"] = int(match.group(1))

    # Title
    title_tag = soup.find("h1", class_="headline-1")
    if title_tag:
        result["title"] = title_tag.get_text(strip=True)

    # Year
    year_tag = soup.find("a", href=re.compile(r"/films/year/\d{4}/"))
    if year_tag:
        year_match = re.search(r"/year/(\d{4})/", year_tag["href"])
        if year_match:
            result["year"] = int(year_match.group(1))

    # Average Letterboxd rating (from the rating histogram data attribute)
    rating_tag = soup.find("meta", attrs={"name": "twitter:data2"})
    if rating_tag and rating_tag.get("content"):
        try:
            result["lb_rating"] = float(rating_tag["content"].split()[0])
        except (ValueError, IndexError):
            pass

    return result


def parse_user_films_page(html: str) -> list[dict]:
    """
    Parse a user's films page (letterboxd.com/{user}/films/ratings/ or /films/).
    Returns list of: {slug, rating}
    """
    soup = BeautifulSoup(html, "lxml")
    films = []

    for li in soup.select("li.poster-container"):
        div = li.find("div", class_="film-poster")
        if not div:
            continue

        slug = div.get("data-film-slug", "").strip("/")
        if not slug:
            continue

        # Rating is stored as data-owner-rating (half-stars: 2 = 1 star, 10 = 5 stars)
        rating_raw = li.get("data-owner-rating") or div.get("data-owner-rating")
        rating = None
        if rating_raw:
            try:
                rating = int(rating_raw) / 2
            except ValueError:
                pass

        films.append({"slug": slug, "rating": rating})

    return films


def parse_film_members_page(html: str) -> list[dict]:
    """
    Parse a film's members/ratings page (letterboxd.com/film/{slug}/members/).
    Returns list of: {username, rating}
    """
    soup = BeautifulSoup(html, "lxml")
    members = []

    for tr in soup.select("tr.film-detail"):
        username_tag = tr.find("td", class_="table-person")
        if not username_tag:
            continue
        a_tag = username_tag.find("a")
        if not a_tag:
            continue
        username = a_tag["href"].strip("/")

        rating_tag = tr.find("span", class_=re.compile(r"rating rated-\d+"))
        rating = None
        if rating_tag:
            classes = rating_tag.get("class", [])
            for cls in classes:
                match = re.match(r"rated-(\d+)", cls)
                if match:
                    rating = int(match.group(1)) / 2
                    break

        members.append({"username": username, "rating": rating})

    return members


def get_total_pages(html: str) -> int:
    """Extract total page count from paginated Letterboxd pages."""
    soup = BeautifulSoup(html, "lxml")
    last_page = soup.select_one("li.paginate-page:last-child a")
    if last_page:
        try:
            return int(last_page.get_text(strip=True))
        except ValueError:
            pass
    return 1
