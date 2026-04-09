"""
Feature-affinity compatibility scoring.

Scores unseen films based on how well their attributes match a user's
demonstrated taste, using four complementary signals:

  1. Genre affinity    — weighted-average rating per TMDB genre (broad strokes)
  2. Keyword affinity  — weighted-average rating per TMDB keyword (thematic detail)
  3. Director affinity — weighted-average rating for films by the same director
  4. Cast affinity     — weighted-average rating for films sharing top cast members

All four signals use temporal decay so that recent ratings carry more weight
than old ones (half-life: 18 months). This means the recommendations track
current taste rather than a flat average across the user's whole history.

Blend weights:  30 % genre  +  45 % keyword  +  15 % director  +  10 % cast
(Within "people" signals, directors carry 2× the weight of cast members.)

Signals are blended from whatever is available — if a film has no keyword data,
genre + people still score it; if a person is unknown, the other signals fill in.

For a group, each member's affinity score is averaged.
"""

import math
from datetime import datetime, timezone
from sqlmodel import Session, select

from app.models.film import Film, FilmGenreLink, Genre, FilmKeyword, FilmKeywordLink, FilmPerson, FilmPersonLink
from app.models.user import LBUser, UserFilmRating

_GENRE_WEIGHT = 0.30
_KEYWORD_WEIGHT = 0.45
_DIRECTOR_WEIGHT = 0.15
_CAST_WEIGHT = 0.10

_HALF_LIFE_DAYS = 547  # ~18 months


def _temporal_weight(watched_at: datetime | None) -> float:
    """
    Exponential decay weight based on how long ago a film was watched.

    Half-life of 18 months: a rating from 18 months ago is worth 0.5×,
    one from 3 years ago is worth 0.25×. Films with no watch date get 1.0
    (no penalty — preserves backward compatibility for existing data).
    """
    if watched_at is None:
        return 1.0
    now = datetime.now(timezone.utc)
    if watched_at.tzinfo is None:
        watched_at = watched_at.replace(tzinfo=timezone.utc)
    age_days = max(0, (now - watched_at).days)
    return math.exp(-age_days * math.log(2) / _HALF_LIFE_DAYS)


def _weighted_avg(pairs: list[tuple[float, float]]) -> float:
    """Weighted average of (value, weight) pairs."""
    total_w = sum(w for _, w in pairs)
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in pairs) / total_w


def build_genre_affinity(session: Session, user_id: int) -> dict[int, float]:
    """Return {genre_id: temporally-weighted avg rating} for genres the user has rated."""
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()
    if not rows:
        return {}
    totals: dict[int, list[tuple[float, float]]] = {}  # genre_id → [(rating, weight)]
    for ufr in rows:
        w = _temporal_weight(ufr.watched_at)
        for gid in session.exec(
            select(FilmGenreLink.genre_id).where(FilmGenreLink.film_id == ufr.film_id)
        ).all():
            totals.setdefault(gid, []).append((ufr.rating, w))
    return {gid: _weighted_avg(pairs) for gid, pairs in totals.items()}


def build_keyword_affinity(session: Session, user_id: int) -> dict[int, float]:
    """Return {keyword_id: temporally-weighted avg rating} for keywords the user has encountered."""
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()
    if not rows:
        return {}
    totals: dict[int, list[tuple[float, float]]] = {}
    for ufr in rows:
        w = _temporal_weight(ufr.watched_at)
        for kid in session.exec(
            select(FilmKeywordLink.keyword_id).where(FilmKeywordLink.film_id == ufr.film_id)
        ).all():
            totals.setdefault(kid, []).append((ufr.rating, w))
    return {kid: _weighted_avg(pairs) for kid, pairs in totals.items()}


def build_director_affinity(session: Session, user_id: int) -> dict[int, float]:
    """Return {person_id: temporally-weighted avg rating} for directors the user has rated."""
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()
    if not rows:
        return {}
    totals: dict[int, list[tuple[float, float]]] = {}
    for ufr in rows:
        w = _temporal_weight(ufr.watched_at)
        for person_id in session.exec(
            select(FilmPersonLink.person_id).where(
                FilmPersonLink.film_id == ufr.film_id,
                FilmPersonLink.role == "director",
            )
        ).all():
            totals.setdefault(person_id, []).append((ufr.rating, w))
    return {pid: _weighted_avg(pairs) for pid, pairs in totals.items()}


def build_cast_affinity(session: Session, user_id: int) -> dict[int, float]:
    """Return {person_id: temporally-weighted avg rating} for cast members the user has rated."""
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()
    if not rows:
        return {}
    totals: dict[int, list[tuple[float, float]]] = {}
    for ufr in rows:
        w = _temporal_weight(ufr.watched_at)
        for person_id in session.exec(
            select(FilmPersonLink.person_id).where(
                FilmPersonLink.film_id == ufr.film_id,
                FilmPersonLink.role == "cast",
            )
        ).all():
            totals.setdefault(person_id, []).append((ufr.rating, w))
    return {pid: _weighted_avg(pairs) for pid, pairs in totals.items()}


def _genre_score_for_film(
    film_id: int,
    genre_affinity: dict[int, float],
    session: Session,
) -> float | None:
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
    kids = session.exec(
        select(FilmKeywordLink.keyword_id).where(FilmKeywordLink.film_id == film_id)
    ).all()
    known = [keyword_affinity[k] for k in kids if k in keyword_affinity]
    if not known:
        return None
    return sum(known) / len(known)


def _people_score_for_film(
    film_id: int,
    director_affinity: dict[int, float],
    cast_affinity: dict[int, float],
    session: Session,
) -> float | None:
    """
    Blend director and cast affinity for this film into a single score.
    Directors are weighted 2× cast members since directorial style is
    a stronger taste signal than individual casting.
    Returns None if no known people are linked to the film.
    """
    links = session.exec(
        select(FilmPersonLink).where(FilmPersonLink.film_id == film_id)
    ).all()
    pairs: list[tuple[float, float]] = []  # (affinity_score, person_weight)
    for link in links:
        if link.role == "director":
            score = director_affinity.get(link.person_id)
            if score is not None:
                pairs.append((score, 2.0))
        elif link.role == "cast":
            score = cast_affinity.get(link.person_id)
            if score is not None:
                pairs.append((score, 1.0))
    if not pairs:
        return None
    return _weighted_avg(pairs)


def _affinity_score(
    film_id: int,
    genre_affinity: dict[int, float],
    keyword_affinity: dict[int, float],
    director_affinity: dict[int, float],
    cast_affinity: dict[int, float],
    global_mean: float,
    session: Session,
) -> float:
    """
    Blend all available affinity signals into a single 0–5 compatibility score.

    Uses only signals that are available for this film, normalising weights
    so they always sum to 1. Falls back to global_mean if no signals fire.
    """
    genre_score = _genre_score_for_film(film_id, genre_affinity, session)
    kw_score = _keyword_score_for_film(film_id, keyword_affinity, session)
    people_score = _people_score_for_film(film_id, director_affinity, cast_affinity, session)

    available: list[tuple[float, float]] = []  # (score, target_weight)
    if genre_score is not None:
        available.append((genre_score, _GENRE_WEIGHT))
    if kw_score is not None:
        available.append((kw_score, _KEYWORD_WEIGHT))
    if people_score is not None:
        available.append((people_score, _DIRECTOR_WEIGHT + _CAST_WEIGHT))

    if not available:
        return global_mean

    total_w = sum(w for _, w in available)
    return sum(s * w for s, w in available) / total_w


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

    user_data = []
    for username in usernames:
        user = session.exec(select(LBUser).where(LBUser.username == username)).first()
        if not user:
            continue
        genre_aff = build_genre_affinity(session, user.id)
        kw_aff = build_keyword_affinity(session, user.id)
        director_aff = build_director_affinity(session, user.id)
        cast_aff = build_cast_affinity(session, user.id)
        all_scores = list(genre_aff.values()) + list(kw_aff.values())
        global_mean = sum(all_scores) / len(all_scores) if all_scores else 3.0
        user_data.append((genre_aff, kw_aff, director_aff, cast_aff, global_mean))

    if not user_data:
        return []

    results: list[tuple[int, float]] = []
    for film_id in candidate_film_ids:
        member_scores = [
            _affinity_score(film_id, ga, ka, da, ca, gm, session)
            for ga, ka, da, ca, gm in user_data
        ]
        results.append((film_id, sum(member_scores) / len(member_scores)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
