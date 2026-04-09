"""
Celery tasks:
  import_profile_task    — scrape one profile (triggered on save + on schedule)
  refresh_all_profiles   — scheduled every 6 hours, queues import for each profile
  run_recommendation_job — on-demand recommendations request from UI
"""

import json
import logging
from datetime import datetime

import httpx
from sqlmodel import Session, select

from app.config import settings
from app.models.db import engine
from app.models.film import Film, Genre, FilmGenreLink, FilmKeyword, FilmKeywordLink, FilmPerson, FilmPersonLink, AppSetting
from app.models.user import LBUser, UserFilmRating
from app.models.job import ScrapeJob
from app.models.profile import UserProfile
from app.scraper.letterboxd_import import parse_letterboxd_zip, fetch_rss_entries
from app.recommender.pipeline import run_group_recommendations
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"


# ---------------------------------------------------------------------------
# ZIP export import
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def process_zip_task(self, profile_id: int, zip_path: str):
    """Process a Letterboxd data-export ZIP and enrich with TMDB metadata."""
    import os

    with Session(engine) as session:
        profile = session.get(UserProfile, profile_id)
        if not profile:
            return

        try:
            with open(zip_path, "rb") as fh:
                zip_bytes = fh.read()

            entries = parse_letterboxd_zip(zip_bytes)

            # Convert export entries to {slug, title, year, rating}
            films = []
            for e in entries:
                lb_uri = e.get("lb_uri", "")
                if lb_uri:
                    slug = lb_uri.rstrip("/").split("/")[-1]
                else:
                    # Fallback: slugify title+year
                    slug = e["title"].lower().replace(" ", "-")
                    if e.get("year"):
                        slug += f"-{e['year']}"
                films.append({
                    "slug": slug,
                    "rating": e.get("rating"),
                    "title": e.get("title"),
                    "year": e.get("year"),
                    "watched_date": e.get("watched_date"),
                })

            _persist_films(session, profile, films)
            _enrich_with_tmdb(session, {profile.username})

            profile.has_data = True
            profile.scrape_status = "ready"
            profile.last_scraped = datetime.utcnow()
            profile.scrape_error = None

        except Exception as exc:
            logger.exception(f"ZIP import failed for {profile.username}: {exc}")
            profile.scrape_status = "error"
            profile.scrape_error = str(exc)

        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass

        session.add(profile)
        session.commit()


# ---------------------------------------------------------------------------
# Scheduled RSS refresh
# ---------------------------------------------------------------------------

@celery_app.task(bind=True)
def compute_embeddings_task(self):
    """
    Incremental task: compute embeddings for any film that doesn't have one yet.

    Dispatches to the configured provider:
      - local  (default): sentence-transformers all-MiniLM-L6-v2, downloaded automatically
      - remote: any OpenAI-compatible embeddings API (LM Studio, Ollama, OpenAI, etc.)

    Input text format: "{title}. {overview}" — used consistently at index and query time.
    Commits every 100 films so progress is visible in the UI.
    """
    import json as _json

    with Session(engine) as session:
        provider = _get_app_setting(session, "embedding_provider", "local")
        if provider == "local":
            try:
                from sentence_transformers import SentenceTransformer  # noqa: F401
            except ImportError:
                logger.error("sentence-transformers not installed")
                return {"status": "error", "detail": "sentence-transformers not installed"}

        _set_app_setting(session, "semantic_matching_computing", "true")
        session.commit()

    with Session(engine) as session:
        film_ids = session.exec(
            select(Film.id).where(Film.embedding == None, Film.overview != None)  # noqa: E711
        ).all()

    if not film_ids:
        logger.info("All films already have embeddings.")
        with Session(engine) as session:
            _set_app_setting(session, "semantic_matching_ready", "true")
            _set_app_setting(session, "semantic_matching_computing", "false")
            session.commit()
        return {"status": "done", "computed": 0}

    logger.info(f"Computing embeddings for {len(film_ids)} films (provider={provider})...")
    total_computed = 0
    CHUNK = 100

    for i in range(0, len(film_ids), CHUNK):
        chunk_ids = film_ids[i : i + CHUNK]
        with Session(engine) as session:
            chunk_films = session.exec(select(Film).where(Film.id.in_(chunk_ids))).all()
            texts = [f"{f.title}. {f.overview}" for f in chunk_films]
            try:
                vectors = _get_embeddings(session, texts)
            except Exception as exc:
                logger.error(f"Embedding error on chunk {i}: {exc}")
                with Session(engine) as s2:
                    _set_app_setting(s2, "semantic_matching_computing", "false")
                    s2.commit()
                return {"status": "error", "detail": str(exc)}
            for film, vec in zip(chunk_films, vectors):
                film.embedding = _json.dumps(vec)
                session.add(film)
            session.commit()
        total_computed += len(chunk_films)
        logger.info(f"Embeddings: {total_computed}/{len(film_ids)} done")

    with Session(engine) as session:
        _set_app_setting(session, "semantic_matching_ready", "true")
        _set_app_setting(session, "semantic_matching_computing", "false")
        session.commit()

    logger.info(f"Embeddings complete: {total_computed} films.")
    return {"status": "done", "computed": total_computed}


def _get_embeddings(session: Session, texts: list[str]) -> list[list[float]]:
    """Dispatch to the configured embedding provider."""
    provider = _get_app_setting(session, "embedding_provider", "local")
    if provider == "remote":
        url = _get_app_setting(session, "embedding_remote_url", "")
        model = _get_app_setting(session, "embedding_remote_model", "")
        api_key = _get_app_setting(session, "embedding_remote_key", "lm-studio")
        if not url or not model:
            raise ValueError("Remote embedding provider requires a URL and model name")
        return _remote_embeddings(url, model, api_key, texts)
    else:
        return _local_embeddings(texts)


def _local_embeddings(texts: list[str]) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vecs = model.encode(texts, batch_size=64, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def _remote_embeddings(url: str, model: str, api_key: str, texts: list[str]) -> list[list[float]]:
    """Call an OpenAI-compatible /v1/embeddings endpoint in batches of 50."""
    base = url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    results: list[list[float]] = []
    BATCH = 50
    for i in range(0, len(texts), BATCH):
        resp = httpx.post(
            f"{base}/embeddings",
            json={"model": model, "input": texts[i : i + BATCH]},
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda x: x["index"])
        results.extend(d["embedding"] for d in data)
    return results


def _get_app_setting(session: Session, key: str, default: str = "") -> str:
    s = session.exec(select(AppSetting).where(AppSetting.key == key)).first()
    return s.value if s else default


def _set_app_setting(session: Session, key: str, value: str):
    existing = session.exec(select(AppSetting).where(AppSetting.key == key)).first()
    if existing:
        existing.value = value
        session.add(existing)
    else:
        session.add(AppSetting(key=key, value=value))


@celery_app.task
def refresh_all_profiles():
    """Queue an RSS refresh for every profile that already has data."""
    with Session(engine) as session:
        profiles = session.exec(
            select(UserProfile).where(UserProfile.has_data == True)  # noqa: E712
        ).all()
        for profile in profiles:
            refresh_profile_rss_task.delay(profile.id)
        logger.info(f"Queued RSS refresh for {len(profiles)} profile(s)")


@celery_app.task
def refresh_profile_rss_task(profile_id: int):
    """Incrementally update a profile from its public Letterboxd RSS feed."""
    with Session(engine) as session:
        profile = session.get(UserProfile, profile_id)
        if not profile:
            return

        try:
            entries = fetch_rss_entries(profile.username)
            films = [
                {"slug": e["slug"], "rating": e.get("rating"), "watched_date": e.get("watched_date")}
                for e in entries
                if e.get("slug")
            ]
            if films:
                _persist_films(session, profile, films)
                _enrich_with_tmdb(session, {profile.username})
                profile.last_scraped = datetime.utcnow()
                session.add(profile)
                session.commit()
                logger.info(f"RSS refresh: {len(films)} entries for {profile.username}")
        except Exception as exc:
            logger.warning(f"RSS refresh failed for {profile.username}: {exc}")


# ---------------------------------------------------------------------------
# On-demand recommendation job
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=1)
def run_recommendation_job(self, job_id: str):
    with Session(engine) as session:
        job = session.exec(select(ScrapeJob).where(ScrapeJob.job_id == job_id)).first()
        if not job:
            return

        job.status = "running"
        session.add(job)
        session.commit()

        try:
            # genre_ids field now stores JSON; fall back to old comma-sep format
            try:
                params = json.loads(job.genre_ids)
                genre_ids = params.get("include", [])
                exclude_genre_ids = params.get("exclude", [])
                min_tmdb_rating = float(params.get("min_rating", 0.0))
            except (json.JSONDecodeError, TypeError, AttributeError):
                genre_ids = [int(g) for g in (job.genre_ids or "").split(",") if g]
                exclude_genre_ids = []
                min_tmdb_rating = 0.0

            profiles = session.exec(select(UserProfile)).all()
            if not profiles:
                raise ValueError("No profiles configured. Visit /setup first.")

            missing = [p.display_name or p.username for p in profiles if not p.has_data]
            if missing:
                raise ValueError(
                    f"Still importing data for: {', '.join(missing)}. "
                    "Please wait for the import to finish."
                )

            usernames = [p.username for p in profiles]
            results = run_group_recommendations(
                session, usernames, genre_ids,
                exclude_genre_ids=exclude_genre_ids,
                min_tmdb_rating=min_tmdb_rating,
            )

            job.status = "complete"
            job.result_json = json.dumps(results)
            job.completed_at = datetime.utcnow()

        except Exception as exc:
            logger.exception(f"Recommendation job {job_id} failed: {exc}")
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()

        session.add(job)
        session.commit()


# ---------------------------------------------------------------------------
# Film persistence helpers
# ---------------------------------------------------------------------------

def _persist_films(session: Session, profile: UserProfile, films: list[dict]):
    """Store scraped {slug, rating} entries for a profile's LBUser."""
    user = session.exec(
        select(LBUser).where(LBUser.username == profile.username)
    ).first()
    if not user:
        user = LBUser(username=profile.username)
        session.add(user)
        session.flush()

    with httpx.Client(timeout=15) as client:
        for entry in films:
            slug = entry["slug"]
            film = session.exec(
                select(Film).where(Film.letterboxd_slug == slug)
            ).first()

            if not film:
                title = entry.get("title") or slug
                year = entry.get("year")
                film = Film(letterboxd_slug=slug, title=title, year=year)
                session.add(film)
                session.flush()

                # Try slug-derived search first (works for readable slugs like
                # "mulholland-drive-2001"), then fall back to title+year from
                # the CSV (catches short-code slugs like "2DjO")
                tmdb_id = _tmdb_search_by_slug(client, slug)
                if not tmdb_id and entry.get("title"):
                    tmdb_id = _tmdb_search(client, entry["title"], entry.get("year"))
                if tmdb_id:
                    data = _tmdb_get_movie(client, tmdb_id)
                    if data:
                        _apply_tmdb_data(session, film, data)
                session.add(film)
                session.flush()

            elif film.tmdb_id is None and entry.get("title"):
                # Stub record from a previous import that used a short-code slug
                # as the title (e.g. "2DjO").  Now that we have the real title
                # from the CSV, update it and attempt TMDB resolution.
                real_title = entry["title"]
                real_year = entry.get("year")
                if film.title == film.letterboxd_slug:
                    film.title = real_title
                if real_year and not film.year:
                    film.year = real_year
                session.add(film)
                session.flush()
                tmdb_id = _tmdb_search(client, real_title, real_year)
                if tmdb_id:
                    data = _tmdb_get_movie(client, tmdb_id)
                    if data:
                        _apply_tmdb_data(session, film, data)
                session.add(film)
                session.flush()

            watched_at = _parse_date(entry.get("watched_date"))
            _upsert_rating(session, user.id, film.id, entry.get("rating"), watched_at)

    user.scraped_at = datetime.utcnow()
    session.add(user)
    session.commit()
    logger.info(f"Persisted {len(films)} films for {profile.username}")


def _enrich_with_tmdb(session: Session, profile_usernames: set[str]):
    """
    - Fill any remaining films missing TMDB metadata.
    - For films rated ≥3.5 by any profile user, fetch TMDB recommendations
      and store them as synthetic signals for collaborative filtering.
    """
    rec_user = session.exec(
        select(LBUser).where(LBUser.username == "__tmdb_recs__")
    ).first()
    if not rec_user:
        rec_user = LBUser(username="__tmdb_recs__", is_audience_user=True)
        session.add(rec_user)
        session.flush()

    all_seen: set[int] = set()
    for uname in profile_usernames:
        u = session.exec(select(LBUser).where(LBUser.username == uname)).first()
        if u:
            all_seen.update(
                r.film_id for r in session.exec(
                    select(UserFilmRating).where(UserFilmRating.user_id == u.id)
                ).all()
            )

    with httpx.Client(timeout=15) as client:
        # Fill missing TMDB data
        for film in session.exec(select(Film).where(Film.tmdb_id == None)).all():
            tmdb_id = _tmdb_search(client, film.title, film.year)
            if tmdb_id:
                data = _tmdb_get_movie(client, tmdb_id)
                if data:
                    _apply_tmdb_data(session, film, data)
            session.add(film)
        session.commit()

        # Fetch full TMDB details (including overview) for candidate films that
        # were created from recommendations and only have minimal data
        for film in session.exec(
            select(Film).where(Film.tmdb_id != None, Film.overview == None)  # noqa: E711
        ).all():
            data = _tmdb_get_movie(client, film.tmdb_id)
            if data:
                _apply_tmdb_data(session, film, data)
                session.add(film)
        session.commit()

        # Fetch keywords and credits for any film that doesn't have them yet
        for film in session.exec(select(Film).where(Film.tmdb_id != None)).all():
            _fetch_and_store_keywords(session, client, film)
            _fetch_and_store_credits(session, client, film)
        session.commit()

        # Build recommendation signals from highly-rated seeds
        high_rated = session.exec(
            select(UserFilmRating, LBUser)
            .join(LBUser, LBUser.id == UserFilmRating.user_id)
            .where(
                LBUser.username.in_(profile_usernames),
                UserFilmRating.rating >= 3.5,
            )
        ).all()

        for ufr, _ in high_rated:
            seed = session.get(Film, ufr.film_id)
            if not seed or not seed.tmdb_id:
                continue
            for rec in _tmdb_get_recommendations(client, seed.tmdb_id):
                rec_film = session.exec(
                    select(Film).where(Film.tmdb_id == rec["tmdb_id"])
                ).first()
                if not rec_film:
                    rec_film = Film(
                        letterboxd_slug=f"tmdb-{rec['tmdb_id']}",
                        tmdb_id=rec["tmdb_id"],
                        title=rec["title"],
                        year=int(rec["year"]) if rec.get("year") else None,
                        poster_url=rec.get("poster_url"),
                        tmdb_rating=rec.get("tmdb_rating"),
                    )
                    session.add(rec_film)
                    session.flush()
                    _apply_genre_ids(session, rec_film, rec.get("genre_ids", []))

                if rec_film.id not in all_seen:
                    implied = min(5.0, (ufr.rating or 3.5) * 0.9)
                    if not session.exec(
                        select(UserFilmRating).where(
                            UserFilmRating.user_id == rec_user.id,
                            UserFilmRating.film_id == rec_film.id,
                        )
                    ).first():
                        session.add(UserFilmRating(
                            user_id=rec_user.id,
                            film_id=rec_film.id,
                            rating=implied,
                        ))
        session.commit()
        logger.info("TMDB enrichment complete")

    # Auto-embed any new films if semantic matching is already enabled
    ready = _get_app_setting(session, "semantic_matching_ready", "false")
    computing = _get_app_setting(session, "semantic_matching_computing", "false")
    if ready == "true" and computing != "true":
        compute_embeddings_task.delay()


# ---------------------------------------------------------------------------
# TMDB helpers
# ---------------------------------------------------------------------------

def _tmdb_search_by_slug(client: httpx.Client, slug: str) -> int | None:
    """Derive a search query from a Letterboxd slug (e.g. 'the-godfather-1972')."""
    import re
    parts = slug.rsplit("-", 1)
    year = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None
    title = parts[0].replace("-", " ") if year else slug.replace("-", " ")
    return _tmdb_search(client, title, year)


def _tmdb_search(client: httpx.Client, title: str, year: int | None) -> int | None:
    try:
        params = {"api_key": settings.tmdb_api_key, "query": title}
        if year:
            params["year"] = year
        r = client.get(f"{TMDB_BASE}/search/movie", params=params)
        results = r.json().get("results", [])
        return results[0]["id"] if results else None
    except Exception:
        return None


def _tmdb_get_movie(client: httpx.Client, tmdb_id: int) -> dict | None:
    try:
        r = client.get(
            f"{TMDB_BASE}/movie/{tmdb_id}",
            params={"api_key": settings.tmdb_api_key},
        )
        r.raise_for_status()
        d = r.json()
        return {
            "tmdb_id": tmdb_id,
            "title": d.get("title", ""),
            "year": (d.get("release_date") or "")[:4] or None,
            "overview": d.get("overview", ""),
            "tmdb_rating": d.get("vote_average"),
            "poster_url": (
                f"{POSTER_BASE}{d['poster_path']}" if d.get("poster_path") else None
            ),
            "genres": [
                {"tmdb_genre_id": g["id"], "name": g["name"]}
                for g in d.get("genres", [])
            ],
        }
    except Exception:
        return None


def _tmdb_get_recommendations(
    client: httpx.Client, tmdb_id: int, pages: int = 3
) -> list[dict]:
    results = []
    for page in range(1, pages + 1):
        try:
            r = client.get(
                f"{TMDB_BASE}/movie/{tmdb_id}/recommendations",
                params={"api_key": settings.tmdb_api_key, "page": page},
            )
            data = r.json().get("results", [])
            if not data:
                break
            for m in data:
                results.append({
                    "tmdb_id": m["id"],
                    "title": m.get("title", ""),
                    "year": (m.get("release_date") or "")[:4] or None,
                    "poster_url": (
                        f"{POSTER_BASE}{m['poster_path']}"
                        if m.get("poster_path") else None
                    ),
                    "tmdb_rating": m.get("vote_average"),
                    "genre_ids": m.get("genre_ids", []),
                })
        except Exception:
            break
    return results


def _upsert_rating(
    session: Session, user_id: int, film_id: int, rating: float | None,
    watched_at: datetime | None = None,
):
    existing = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.film_id == film_id,
        )
    ).first()
    if existing:
        if rating is not None:
            existing.rating = rating
        if watched_at is not None and (existing.watched_at is None or watched_at > existing.watched_at):
            existing.watched_at = watched_at
        session.add(existing)
    else:
        session.add(
            UserFilmRating(user_id=user_id, film_id=film_id, rating=rating, watched_at=watched_at)
        )


def _apply_tmdb_data(session: Session, film: Film, data: dict):
    film.tmdb_id = data.get("tmdb_id") or film.tmdb_id
    film.title = data.get("title") or film.title
    film.year = int(data["year"]) if data.get("year") else film.year
    film.overview = data.get("overview")
    film.tmdb_rating = data.get("tmdb_rating")
    film.poster_url = data.get("poster_url")
    for g in data.get("genres", []):
        genre = session.exec(
            select(Genre).where(Genre.tmdb_genre_id == g["tmdb_genre_id"])
        ).first()
        if not genre:
            genre = Genre(tmdb_genre_id=g["tmdb_genre_id"], name=g["name"])
            session.add(genre)
            session.flush()
        if not session.exec(
            select(FilmGenreLink).where(
                FilmGenreLink.film_id == film.id,
                FilmGenreLink.genre_id == genre.id,
            )
        ).first():
            session.add(FilmGenreLink(film_id=film.id, genre_id=genre.id))


def _fetch_and_store_keywords(session: Session, client: httpx.Client, film: Film):
    """Fetch TMDB keywords for a film and persist them if not already stored."""
    if not film.tmdb_id:
        return
    already = session.exec(
        select(FilmKeywordLink).where(FilmKeywordLink.film_id == film.id)
    ).first()
    if already:
        return  # already fetched
    try:
        r = client.get(
            f"{TMDB_BASE}/movie/{film.tmdb_id}/keywords",
            params={"api_key": settings.tmdb_api_key},
        )
        r.raise_for_status()
        keywords = r.json().get("keywords", [])
    except Exception:
        return
    for kw in keywords:
        keyword = session.exec(
            select(FilmKeyword).where(FilmKeyword.tmdb_keyword_id == kw["id"])
        ).first()
        if not keyword:
            keyword = FilmKeyword(tmdb_keyword_id=kw["id"], name=kw["name"])
            session.add(keyword)
            session.flush()
        if not session.exec(
            select(FilmKeywordLink).where(
                FilmKeywordLink.film_id == film.id,
                FilmKeywordLink.keyword_id == keyword.id,
            )
        ).first():
            session.add(FilmKeywordLink(film_id=film.id, keyword_id=keyword.id))


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a 'YYYY-MM-DD' date string into a UTC-naive datetime, or return None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None


def _fetch_and_store_credits(session: Session, client: httpx.Client, film: Film):
    """Fetch TMDB credits (director + top 3 cast) and persist if not already stored."""
    if not film.tmdb_id:
        return
    already = session.exec(
        select(FilmPersonLink).where(FilmPersonLink.film_id == film.id)
    ).first()
    if already:
        return
    try:
        r = client.get(
            f"{TMDB_BASE}/movie/{film.tmdb_id}/credits",
            params={"api_key": settings.tmdb_api_key},
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return

    people: list[tuple[int, str, str]] = []  # (tmdb_person_id, name, role)
    for member in data.get("crew", []):
        if member.get("job") == "Director":
            people.append((member["id"], member["name"], "director"))
    for member in data.get("cast", [])[:3]:  # top 3 billed cast
        people.append((member["id"], member["name"], "cast"))

    for tmdb_person_id, name, role in people:
        person = session.exec(
            select(FilmPerson).where(FilmPerson.tmdb_person_id == tmdb_person_id)
        ).first()
        if not person:
            person = FilmPerson(tmdb_person_id=tmdb_person_id, name=name)
            session.add(person)
            session.flush()
        if not session.exec(
            select(FilmPersonLink).where(
                FilmPersonLink.film_id == film.id,
                FilmPersonLink.person_id == person.id,
                FilmPersonLink.role == role,
            )
        ).first():
            session.add(FilmPersonLink(film_id=film.id, person_id=person.id, role=role))


_TMDB_GENRE_NAMES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance",
    878: "Science Fiction", 53: "Thriller", 10752: "War", 37: "Western",
}


def _apply_genre_ids(session: Session, film: Film, genre_ids: list[int]):
    for gid in genre_ids:
        genre = session.exec(
            select(Genre).where(Genre.tmdb_genre_id == gid)
        ).first()
        if not genre:
            name = _TMDB_GENRE_NAMES.get(gid, f"Genre {gid}")
            genre = Genre(tmdb_genre_id=gid, name=name)
            session.add(genre)
            session.flush()
        if not session.exec(
            select(FilmGenreLink).where(
                FilmGenreLink.film_id == film.id,
                FilmGenreLink.genre_id == genre.id,
            )
        ).first():
            session.add(FilmGenreLink(film_id=film.id, genre_id=genre.id))
