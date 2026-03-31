"""
Cold-start fallback: recommend popular films in the requested genre(s)
when the user has too few ratings for collaborative filtering.
"""

from sqlmodel import Session, select
from app.models.film import Film, FilmGenreLink, Genre


def cold_start_recommendations(
    session: Session,
    genre_ids: list[int],
    seen_film_ids: set[int],
    top_n: int = 20,
    min_tmdb_rating: float = 0.0,
) -> list[dict]:
    """
    Return top-N films by TMDB rating, filtered by genre and not yet seen.
    """
    genre_filter_ids: set[int] = set()
    if genre_ids:
        genre_db_ids = session.exec(
            select(Genre.id).where(Genre.tmdb_genre_id.in_(genre_ids))
        ).all()
        film_ids_in_genre = session.exec(
            select(FilmGenreLink.film_id).where(
                FilmGenreLink.genre_id.in_(genre_db_ids)
            )
        ).all()
        genre_filter_ids = set(film_ids_in_genre)

    query = select(Film).where(Film.tmdb_rating.isnot(None))
    films = session.exec(query).all()

    results = []
    for film in films:
        if film.id in seen_film_ids:
            continue
        if genre_ids and film.id not in genre_filter_ids:
            continue
        if min_tmdb_rating > 0 and (film.tmdb_rating or 0) < min_tmdb_rating:
            continue
        results.append(film)

    results.sort(key=lambda f: f.tmdb_rating or 0, reverse=True)
    return [_film_to_dict(f, score=None) for f in results[:top_n]]


def _film_to_dict(film: Film, score: float | None) -> dict:
    return {
        "film_id": film.id,
        "title": film.title,
        "year": film.year,
        "poster_url": film.poster_url,
        "overview": film.overview,
        "tmdb_rating": film.tmdb_rating,
        "predicted_score": score,
        "letterboxd_url": f"https://letterboxd.com/film/{film.letterboxd_slug}/",
    }
