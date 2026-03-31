"""
Celery task: scrape a user's Letterboxd ratings and enrich with TMDB data,
then collect audience ratings for collaborative filtering,
then run the recommendation pipeline.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import settings
from app.models.db import engine
from app.models.film import Film, Genre, FilmGenreLink
from app.models.user import LBUser, UserFilmRating
from app.models.job import ScrapeJob
from app.scraper.letterboxd import LetterboxdScraper
from app.tmdb.client import TMDBClient
from app.recommender.pipeline import run_recommendations
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def run_recommendation_job(self, job_id: str):
    """Main Celery task that drives the full pipeline."""
    asyncio.run(_async_run(job_id))


async def _async_run(job_id: str):
    with Session(engine) as session:
        job = session.exec(select(ScrapeJob).where(ScrapeJob.job_id == job_id)).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        job.status = "running"
        session.add(job)
        session.commit()

        try:
            genre_ids = (
                [int(g) for g in job.genre_ids.split(",") if g]
                if job.genre_ids
                else []
            )

            await _scrape_user(session, job.username)
            await _scrape_audience(session, job.username)

            results = run_recommendations(session, job.username, genre_ids)

            job.status = "complete"
            job.result_json = json.dumps(results)
            job.completed_at = datetime.utcnow()
        except Exception as exc:
            logger.exception(f"Job {job_id} failed: {exc}")
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()

        session.add(job)
        session.commit()


async def _scrape_user(session: Session, username: str):
    """Scrape user ratings and enrich with TMDB. Skips if recently cached."""
    user = session.exec(select(LBUser).where(LBUser.username == username)).first()
    cutoff = datetime.utcnow() - timedelta(hours=settings.scrape_cache_hours)

    if user and user.scraped_at and user.scraped_at > cutoff:
        logger.info(f"Cache hit for {username}, skipping scrape")
        return

    async with LetterboxdScraper() as lb, TMDBClient() as tmdb:
        raw_films = await lb.get_user_ratings(username)

        if not user:
            user = LBUser(username=username)
            session.add(user)
            session.flush()

        existing_slugs = {
            slug
            for (slug,) in session.exec(select(Film.letterboxd_slug)).all()
        }

        for film_data in raw_films:
            slug = film_data["slug"]

            # Upsert film record
            film = session.exec(
                select(Film).where(Film.letterboxd_slug == slug)
            ).first()

            if not film:
                film = Film(letterboxd_slug=slug, title=slug)  # title updated below
                session.add(film)
                session.flush()

            # Enrich from Letterboxd film page if no TMDB ID yet
            if not film.tmdb_id:
                lb_details = await lb.get_film_details(slug)
                tmdb_id = lb_details.get("tmdb_id")

                # Fallback: search TMDB by title + year
                if not tmdb_id and lb_details.get("title"):
                    tmdb_id = await tmdb.search_movie(
                        lb_details["title"], lb_details.get("year")
                    )

                if tmdb_id:
                    film.tmdb_id = tmdb_id
                    tmdb_data = await tmdb.get_movie(tmdb_id)
                    if tmdb_data:
                        _apply_tmdb(session, film, tmdb_data)

                if lb_details.get("title"):
                    film.title = lb_details["title"]
                if lb_details.get("year"):
                    film.year = lb_details["year"]
                if lb_details.get("lb_rating"):
                    film.lb_rating = lb_details["lb_rating"]

                session.add(film)
                session.flush()

            # Upsert rating
            rating_record = session.exec(
                select(UserFilmRating).where(
                    UserFilmRating.user_id == user.id,
                    UserFilmRating.film_id == film.id,
                )
            ).first()

            if not rating_record:
                rating_record = UserFilmRating(
                    user_id=user.id,
                    film_id=film.id,
                    rating=film_data.get("rating"),
                )
                session.add(rating_record)
            else:
                rating_record.rating = film_data.get("rating")
                session.add(rating_record)

        user.scraped_at = datetime.utcnow()
        user.film_count = len(raw_films)
        session.add(user)
        session.commit()
        logger.info(f"Scraped {len(raw_films)} films for {username}")


def _apply_tmdb(session: Session, film: Film, tmdb_data: dict):
    """Apply TMDB metadata to a film record, including genre upserts."""
    film.title = tmdb_data.get("title") or film.title
    film.year = int(tmdb_data["year"]) if tmdb_data.get("year") else film.year
    film.overview = tmdb_data.get("overview")
    film.tmdb_rating = tmdb_data.get("tmdb_rating")
    film.poster_url = tmdb_data.get("poster_url")

    for g in tmdb_data.get("genres", []):
        genre = session.exec(
            select(Genre).where(Genre.tmdb_genre_id == g["tmdb_genre_id"])
        ).first()
        if not genre:
            genre = Genre(tmdb_genre_id=g["tmdb_genre_id"], name=g["name"])
            session.add(genre)
            session.flush()

        link = session.exec(
            select(FilmGenreLink).where(
                FilmGenreLink.film_id == film.id,
                FilmGenreLink.genre_id == genre.id,
            )
        ).first()
        if not link:
            session.add(FilmGenreLink(film_id=film.id, genre_id=genre.id))


async def _scrape_audience(session: Session, username: str):
    """
    For each film the user has rated, scrape ratings from other Letterboxd members.
    This populates the ratings matrix for collaborative filtering.
    """
    user = session.exec(select(LBUser).where(LBUser.username == username)).first()
    if not user:
        return

    user_film_ids = [
        r.film_id
        for r in session.exec(
            select(UserFilmRating).where(UserFilmRating.user_id == user.id)
        ).all()
    ]

    films = [session.get(Film, fid) for fid in user_film_ids]
    films = [f for f in films if f]

    async with LetterboxdScraper() as lb:
        for film in films:
            audience = await lb.get_film_audience_ratings(
                film.letterboxd_slug, max_pages=settings.cf_max_audience_pages
            )

            for member in audience:
                au_username = member["username"]
                if au_username == username:
                    continue

                # Upsert audience user
                au_user = session.exec(
                    select(LBUser).where(LBUser.username == au_username)
                ).first()
                if not au_user:
                    au_user = LBUser(username=au_username, is_audience_user=True)
                    session.add(au_user)
                    session.flush()

                # Upsert rating
                existing = session.exec(
                    select(UserFilmRating).where(
                        UserFilmRating.user_id == au_user.id,
                        UserFilmRating.film_id == film.id,
                    )
                ).first()
                if not existing:
                    session.add(
                        UserFilmRating(
                            user_id=au_user.id,
                            film_id=film.id,
                            rating=member.get("rating"),
                        )
                    )

            session.commit()
            logger.info(f"Audience scraped for {film.letterboxd_slug}: {len(audience)} members")
