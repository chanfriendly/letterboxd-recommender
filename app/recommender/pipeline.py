"""
Recommendation pipeline — single-user and N-user group modes.
"""

from sqlmodel import Session, select
from app.config import settings
from app.models.film import Film
from app.models.user import LBUser, UserFilmRating
from app.recommender.collaborative import (
    build_sparse_matrix,
    find_similar_users,
    score_unseen_films,
)
from app.recommender.content_filter import get_films_by_genres
from app.recommender.fallback import cold_start_recommendations, _film_to_dict


def run_group_recommendations(
    session: Session,
    usernames: list[str],
    genre_ids: list[int],
    top_n: int = 20,
) -> list[dict]:
    """
    Generate recommendations for a group of 1–N users.

    - All films seen (rated OR unrated) by ANY member are excluded from results.
    - Each candidate film is scored independently for each member.
    - Final score = average of all members' predicted scores.
      Films suggested by only some members are down-weighted.
    """
    if not usernames:
        return []

    if len(usernames) == 1:
        return _run_single(session, usernames[0], genre_ids, top_n)

    users = [
        session.exec(select(LBUser).where(LBUser.username == u)).first()
        for u in usernames
    ]
    users = [u for u in users if u]
    if not users:
        return []

    # Combined seen set — rated AND unrated (rating IS NULL counts as watched)
    seen_per_user: list[set[int]] = []
    for user in users:
        seen = {
            r.film_id for r in session.exec(
                select(UserFilmRating).where(UserFilmRating.user_id == user.id)
            ).all()
        }
        seen_per_user.append(seen)

    seen_combined = set().union(*seen_per_user)

    rated_counts = [
        sum(1 for r in session.exec(
            select(UserFilmRating).where(
                UserFilmRating.user_id == u.id, UserFilmRating.rating != None
            )
        ).all())
        for u in users
    ]

    if any(c < settings.cf_cold_start_threshold for c in rated_counts):
        return cold_start_recommendations(session, genre_ids, seen_combined, top_n)

    candidate_film_ids = get_films_by_genres(session, genre_ids)
    candidate_film_ids -= seen_combined
    if not candidate_film_ids:
        return cold_start_recommendations(session, genre_ids, seen_combined, top_n)

    ratings_flat = _load_all_ratings(session)
    if not ratings_flat:
        return cold_start_recommendations(session, genre_ids, seen_combined, top_n)

    matrix, all_usernames, film_ids = build_sparse_matrix(ratings_flat)

    # Score candidates for each user independently
    per_user_scores: list[dict[int, float]] = []
    for user in users:
        similar = find_similar_users(user.username, matrix, all_usernames, top_k=50)
        scored = score_unseen_films(
            target_username=user.username,
            similar_users=similar,
            matrix=matrix,
            usernames=all_usernames,
            film_ids=film_ids,
            seen_film_ids=seen_combined,
            candidate_film_ids=candidate_film_ids,
        )
        per_user_scores.append(dict(scored))

    # Combine: films scored by all members get full average;
    # films scored by only some get proportionally less weight
    all_candidates = set().union(*per_user_scores)
    combined: list[tuple[int, float]] = []
    n = len(users)
    for fid in all_candidates:
        scores = [s[fid] for s in per_user_scores if fid in s]
        # Weight by fraction of members who have a score for this film
        weight = len(scores) / n
        avg = sum(scores) / len(scores)
        combined.append((fid, avg * weight))

    combined.sort(key=lambda x: x[1], reverse=True)
    return _enrich(session, combined[:top_n])


def run_recommendations(
    session: Session,
    username: str,
    genre_ids: list[int],
    top_n: int = 20,
) -> list[dict]:
    return _run_single(session, username, genre_ids, top_n)


def _run_single(
    session: Session,
    username: str,
    genre_ids: list[int],
    top_n: int,
) -> list[dict]:
    user = session.exec(select(LBUser).where(LBUser.username == username)).first()
    if not user:
        return []

    seen_film_ids = {
        r.film_id for r in session.exec(
            select(UserFilmRating).where(UserFilmRating.user_id == user.id)
        ).all()
    }

    rated_count = sum(1 for r in session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user.id, UserFilmRating.rating != None
        )
    ).all())

    if rated_count < settings.cf_cold_start_threshold:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    candidate_film_ids = get_films_by_genres(session, genre_ids)
    candidate_film_ids -= seen_film_ids
    if not candidate_film_ids:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    ratings_flat = _load_all_ratings(session)
    if not ratings_flat:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    matrix, usernames, film_ids = build_sparse_matrix(ratings_flat)
    similar = find_similar_users(username, matrix, usernames, top_k=50)
    if not similar:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    scored = score_unseen_films(
        target_username=username,
        similar_users=similar,
        matrix=matrix,
        usernames=usernames,
        film_ids=film_ids,
        seen_film_ids=seen_film_ids,
        candidate_film_ids=candidate_film_ids,
    )
    return _enrich(session, scored[:top_n])


def _load_all_ratings(session: Session) -> list[dict]:
    all_raw = session.exec(
        select(UserFilmRating, LBUser).join(LBUser, LBUser.id == UserFilmRating.user_id)
    ).all()
    return [
        {"username": u.username, "film_id": r.film_id, "rating": r.rating}
        for r, u in all_raw
        if r.rating is not None
    ]


def _enrich(session: Session, scored: list[tuple[int, float]]) -> list[dict]:
    results = []
    for film_id, score in scored:
        film = session.get(Film, film_id)
        if film:
            results.append(_film_to_dict(film, score))
    return results
