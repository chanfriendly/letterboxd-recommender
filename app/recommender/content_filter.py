"""
Content-based filtering: genre match + exclude already-seen films.
"""

from sqlmodel import Session, select
from app.models.film import Film, FilmGenreLink


def get_films_by_genres(
    session: Session,
    genre_ids: list[int],
    exclude_genre_ids: list[int] | None = None,
) -> set[int]:
    """
    Return film IDs matching any included genre, minus any excluded genre.
    If genre_ids is empty, all films are candidates (before exclusion).
    """
    from app.models.film import Genre

    if not genre_ids:
        all_ids = session.exec(select(Film.id)).all()
        candidates = set(all_ids)
    else:
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
        candidates = set(film_ids)

    if exclude_genre_ids:
        excl_db_ids = session.exec(
            select(Genre.id).where(Genre.tmdb_genre_id.in_(exclude_genre_ids))
        ).all()
        if excl_db_ids:
            excl_film_ids = session.exec(
                select(FilmGenreLink.film_id).where(
                    FilmGenreLink.genre_id.in_(excl_db_ids)
                )
            ).all()
            candidates -= set(excl_film_ids)

    return candidates
