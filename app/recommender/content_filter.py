"""
Content-based filtering: genre match + exclude already-seen films.
"""

from sqlmodel import Session, select
from app.models.film import Film, FilmGenreLink


def get_films_by_genres(session: Session, genre_ids: list[int]) -> set[int]:
    """Return set of film IDs that match any of the requested TMDB genre IDs."""
    if not genre_ids:
        # No genre filter — return all films
        all_ids = session.exec(select(Film.id)).all()
        return set(all_ids)

    from app.models.film import Genre
    genre_db_ids = session.exec(
        select(Genre.id).where(Genre.tmdb_genre_id.in_(genre_ids))
    ).all()

    if not genre_db_ids:
        return set()

    film_ids = session.exec(
        select(FilmGenreLink.film_id).where(
            FilmGenreLink.genre_id.in_(genre_db_ids)
        )
    ).all()
    return set(film_ids)
