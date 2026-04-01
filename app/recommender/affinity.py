"""
Feature-affinity compatibility scoring.

Scores unseen films based on how well their attributes match a user's
demonstrated taste, using two complementary signals:

  1. Genre affinity  — average rating per TMDB genre (broad strokes)
  2. Keyword affinity — average rating per TMDB keyword (thematic detail)

Keywords let the app follow thematic throughlines that cross genre boundaries.
For example, a user who loves "Platoon", "Full Metal Jacket", and "Come and See"
will have high affinity for keywords like "anti-war", "psychological trauma",
and "moral ambiguity" — and will receive high scores for films carrying those
keywords even if they're categorised under different genres.

Blend weights:  40 % genre  +  60 % keyword
(Keywords get more weight because they carry more semantic specificity.
 Genre acts as a broad prior when keyword coverage is thin.)

For a group, each member's affinity score is averaged.
"""

from sqlmodel import Session, select

from app.models.film import Film, FilmGenreLink, Genre, FilmKeyword, FilmKeywordLink
from app.models.user import LBUser, UserFilmRating

_GENRE_WEIGHT = 0.4
_KEYWORD_WEIGHT = 0.6


def build_genre_affinity(session: Session, user_id: int) -> dict[int, float]:
    """Return {genre_id: avg_rating} for every genre the user has rated."""
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()
    if not rows:
        return {}
    totals: dict[int, list[float]] = {}
    for ufr in rows:
        for gid in session.exec(
            select(FilmGenreLink.genre_id).where(FilmGenreLink.film_id == ufr.film_id)
        ).all():
            totals.setdefault(gid, []).append(ufr.rating)
    return {gid: sum(v) / len(v) for gid, v in totals.items()}


def build_keyword_affinity(session: Session, user_id: int) -> dict[int, float]:
    """Return {keyword_id: avg_rating} for every keyword the user has encountered."""
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()
    if not rows:
        return {}
    totals: dict[int, list[float]] = {}
    for ufr in rows:
        for kid in session.exec(
            select(FilmKeywordLink.keyword_id).where(FilmKeywordLink.film_id == ufr.film_id)
        ).all():
            totals.setdefault(kid, []).append(ufr.rating)
    return {kid: sum(v) / len(v) for kid, v in totals.items()}


def _genre_score_for_film(
    film_id: int,
    genre_affinity: dict[int, float],
    global_mean: float,
    session: Session,
) -> float | None:
    """Return avg genre affinity for this film, or None if no genres are known."""
    gids = session.exec(
        select(FilmGenreLink.genre_id).where(FilmGenreLink.film_id == film_id)
    ).all()
    known = [genre_affinity[g] for g in gids if g in genre_affinity]
    if not known:
        return None
    return sum(known) / len(known)


def _keyword_score_for_film(
    film_id: int,
    keyword_affinity: dict[int, float],
    session: Session,
) -> float | None:
    """Return avg keyword affinity for this film, or None if no keywords are known."""
    kids = session.exec(
        select(FilmKeywordLink.keyword_id).where(FilmKeywordLink.film_id == film_id)
    ).all()
    known = [keyword_affinity[k] for k in kids if k in keyword_affinity]
    if not known:
        return None
    return sum(known) / len(known)


def _affinity_score(
    film_id: int,
    genre_affinity: dict[int, float],
    keyword_affinity: dict[int, float],
    global_mean: float,
    session: Session,
) -> float:
    """
    Blend genre and keyword affinity into a single 0–5 compatibility score.

    If only one signal is available, use it at full weight.
    If neither is available, fall back to global_mean.
    """
    genre_score = _genre_score_for_film(film_id, genre_affinity, global_mean, session)
    kw_score = _keyword_score_for_film(film_id, keyword_affinity, session)

    if genre_score is not None and kw_score is not None:
        return _GENRE_WEIGHT * genre_score + _KEYWORD_WEIGHT * kw_score
    if kw_score is not None:
        return kw_score
    if genre_score is not None:
        return genre_score
    return global_mean


def score_candidates_by_affinity(
    session: Session,
    usernames: list[str],
    candidate_film_ids: set[int],
) -> list[tuple[int, float]]:
    """
    Score each candidate film for a group of users by feature affinity.

    Returns (film_id, compatibility_score) sorted descending.
    """
    if not usernames or not candidate_film_ids:
        return []

    user_data: list[tuple[dict, dict, float]] = []  # (genre_aff, kw_aff, global_mean)
    for username in usernames:
        user = session.exec(select(LBUser).where(LBUser.username == username)).first()
        if not user:
            continue
        genre_aff = build_genre_affinity(session, user.id)
        kw_aff = build_keyword_affinity(session, user.id)
        all_affinities = list(genre_aff.values()) + list(kw_aff.values())
        global_mean = sum(all_affinities) / len(all_affinities) if all_affinities else 3.0
        user_data.append((genre_aff, kw_aff, global_mean))

    if not user_data:
        return []

    results: list[tuple[int, float]] = []
    for film_id in candidate_film_ids:
        member_scores = [
            _affinity_score(film_id, ga, ka, gm, session)
            for ga, ka, gm in user_data
        ]
        results.append((film_id, sum(member_scores) / len(member_scores)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
