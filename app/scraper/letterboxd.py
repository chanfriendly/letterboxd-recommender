"""
Letterboxd scraper using Playwright (headless Firefox).
Firefox bypasses Cloudflare's bot detection better than headless Chromium,
which is heavily fingerprinted by Cloudflare's automation checks.
"""

import logging
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)


def scrape_user_films(username: str, password: str) -> list[dict]:
    """
    Log in to Letterboxd and return all watched films.
    Each entry: {slug, rating}  — rating is None for watched-but-unrated films.
    """
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
                "Gecko/20100101 Firefox/125.0"
            ),
            locale="en-US",
        )
        page = ctx.new_page()
        try:
            _login(page, username, password)
            films = _scrape_all_pages(page, username)
            logger.info(f"Scraped {len(films)} films for {username}")
            return films
        finally:
            browser.close()


def _login(page, username: str, password: str):
    page.goto("https://letterboxd.com/sign-in/", wait_until="domcontentloaded")

    # Wait for the form fields — gives Cloudflare JS challenge time to clear
    try:
        page.wait_for_selector('input[name="username"]', timeout=30_000)
    except PWTimeout:
        raise ValueError(
            "Sign-in page didn't load — Letterboxd may be blocking the request. "
            "Try again in a moment."
        )

    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)

    submit = page.locator('button[type="submit"], input[type="submit"]').first
    submit.click()

    # Wait up to 60 s for the URL to leave /sign-in
    try:
        page.wait_for_url(
            lambda url: "/sign-in" not in url,
            timeout=60_000,
            wait_until="domcontentloaded",
        )
    except PWTimeout:
        logger.warning("Login redirect timed out; current URL: %s", page.url)
        raise ValueError(
            f"Login timed out for '{username}'. "
            "Letterboxd may be slow or blocking the request — try again in a moment."
        )

    if "sign-in" in page.url:
        error = page.query_selector(".form-error, [data-error]")
        msg = error.inner_text().strip() if error else "Incorrect username or password"
        raise ValueError(f"Login failed for '{username}': {msg}")

    logger.info(f"Logged in as {username}")


def _scrape_all_pages(page, username: str) -> list[dict]:
    """Paginate through /username/films/ and collect all film slugs + ratings."""
    films: list[dict] = []
    page_num = 1

    while True:
        url = (
            f"https://letterboxd.com/{username}/films/"
            if page_num == 1
            else f"https://letterboxd.com/{username}/films/page/{page_num}/"
        )
        page.goto(url, wait_until="domcontentloaded")

        items = page.query_selector_all("li.poster-container")
        if not items:
            break

        for item in items:
            poster = item.query_selector("div.film-poster")
            if not poster:
                continue
            slug = poster.get_attribute("data-film-slug")
            if not slug:
                continue

            rating_raw = item.get_attribute("data-owner-rating")
            rating = None
            if rating_raw and rating_raw != "0":
                try:
                    rating = int(rating_raw) / 2  # Letterboxd stores half-stars as ints
                except ValueError:
                    pass

            films.append({"slug": slug, "rating": rating})

        # Stop if no "next page" link
        if not page.query_selector("a.next"):
            break

        page_num += 1
        time.sleep(0.8)  # polite pacing

    return films
