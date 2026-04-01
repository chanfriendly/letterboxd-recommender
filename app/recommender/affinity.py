"""
Genre-affinity compatibility scoring.

Computes a predicted 0–5 score for an unseen film based on how highly the
user has rated films in the same genre(s) historically.  This works even when
traditional collaborative filtering can't fire (too few mutual ratings between
users), so it replaces the raw TMDB-rating fallback with something personalised.

For a group, each member's affinity score is averaged.  The result is labelled
"Match" just like CF scores so the UI treats it identically.
"""

from sqlmodel import Session, select

from app.models.film import Film, FilmGenreLink, Genre
from app.models.user import LBUser, UserFilmRating


def build_genre_affinity(session: Session, user_id: int) -> dict[int, float]:
    """
    Return {genre_id: avg_rating} for every genre this user has rated at least
    one film in.  Only rated films (rating IS NOT NULL) contribute.
    """
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()

    if not rows:
        return {}

    genre_totals: dict[int, list[float]] = {}
    for ufr in rows:
        genre_ids = session.exec(
            select(FilmGenreLink.genre_id).where(FilmGenreLink.film_id == ufr.film_id)
        ).all()
        for gid in genre_ids:
            genre_totals.setdefault(gid, []).append(ufr.rating)

    return {gid: sum(ratings) / len(ratings) for gid, ratings in genre_totals.items()}


def affinity_score_for_film(
    session: Session,
    film: Film,
    genre_affinities: dict[int, float],
    global_mean: float,
) -> float:
    """
    Predict a 0–5 score for *film* given a user's genre affinity map.

    Algorithm:
      - Collect the affinity scores for each of the film's genres.
      - If the film has genres the user has rated before, return their average.
      - If the film has genres but none that the user has seen, return global_mean.
      - If the film has no genre data at all, return global_mean.

    This keeps the score on the same 0–5 scale as real ratings.
    """
    film_genre_ids = session.exec(
        select(FilmGenreLink.genre_id).where(FilmGenreLink.film_id == film.id)
    ).all()

    known = [genre_affinities[gid] for gid in film_genre_ids if gid in genre_affinities]
    if known:
        return sum(known) / len(known)
    return global_mean


def score_candidates_by_affinity(
    session: Session,
    usernames: list[str],
    candidate_film_ids: set[int],
) -> list[tuple[int, float]]:
    """
    Score each candidate film for a group of users by genre affinity.

    Returns a list of (film_id, compatibility_score) sorted descending,
    ready to drop straight into _enrich().
    """
    if not usernames or not candidate_film_ids:
        return []

    # Build per-user affinity maps and compute each user's global mean
    user_affinities: list[dict[int, float]] = []
    global_means: list[float] = []

    for username in usernames:
        user = session.exec(select(LBUser).where(LBUser.username == username)).first()
        if not user:
            continue
        affinity = build_genre_affinity(session, user.id)
        user_affinities.append(affinity)
        global_means.append(
            sum(affinity.values()) / len(affinity) if affinity else 3.0
        )

    if not user_affinities:
        return []

    results: list[tuple[int, float]] = []
    for film_id in candidate_film_ids:
        film = session.get(Film, film_id)
        if not film:
            continue

        member_scores = [
            affinity_score_for_film(session, film, aff, gm)
            for aff, gm in zip(user_affinities, global_means)
        ]
        results.append((film_id, sum(member_scores) / len(member_scores)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
