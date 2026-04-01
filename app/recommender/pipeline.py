"""
Recommendation pipeline — single-user and N-user group modes.
"""

from sqlmodel import Session, select
from app.config import settings
from app.models.film import Film, VetoedFilm
from app.models.user import LBUser, UserFilmRating


def _load_vetoed_film_ids(session: Session) -> set[int]:
    return {v.film_id for v in session.exec(select(VetoedFilm)).all()}


def _expand_seen_by_tmdb_id(session: Session, seen_film_ids: set[int]) -> set[int]:
    """
    Expand a set of seen film PKs to include all films sharing the same tmdb_id.

    Prevents synthetic tmdb-{id} duplicates of already-watched films from
    appearing in recommendations when the original slug-based film was seen.
    """
    if not seen_film_ids:
        return seen_film_ids
    seen_films = session.exec(
        select(Film).where(Film.id.in_(seen_film_ids))
    ).all()
    tmdb_ids = {f.tmdb_id for f in seen_films if f.tmdb_id is not None}
    if not tmdb_ids:
        return seen_film_ids
    duplicates = session.exec(
        select(Film.id).where(Film.tmdb_id.in_(tmdb_ids))
    ).all()
    return seen_film_ids | set(duplicates)
from app.recommender.affinity import score_candidates_by_affinity
from app.recommender.semantic import score_candidates_by_embedding, semantic_matching_enabled
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
    exclude_genre_ids: list[int] | None = None,
    min_tmdb_rating: float = 0.0,
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
        return _run_single(session, usernames[0], genre_ids, exclude_genre_ids or [], min_tmdb_rating, top_n)

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

    seen_combined = _expand_seen_by_tmdb_id(session, set().union(*seen_per_user))
    seen_combined |= _load_vetoed_film_ids(session)

    rated_counts = [
        sum(1 for r in session.exec(
            select(UserFilmRating).where(
                UserFilmRating.user_id == u.id, UserFilmRating.rating != None
            )
        ).all())
        for u in users
    ]

    candidate_film_ids = get_films_by_genres(session, genre_ids, exclude_genre_ids)
    candidate_film_ids -= seen_combined

    if any(c < settings.cf_cold_start_threshold for c in rated_counts) or not candidate_film_ids:
        return _affinity_then_cold_start(session, usernames, genre_ids, exclude_genre_ids or [], candidate_film_ids, seen_combined, top_n, min_tmdb_rating)

    ratings_flat = _load_all_ratings(session)
    if not ratings_flat:
        return _affinity_then_cold_start(session, usernames, genre_ids, exclude_genre_ids or [], candidate_film_ids, seen_combined, top_n, min_tmdb_rating)

    matrix, all_usernames, film_ids, user_means = build_sparse_matrix(ratings_flat)

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
            user_means=user_means,
        )
        per_user_scores.append(dict(scored))

    # Combine: prefer films with coverage across all members, then by average score.
    # Score stays on the 0–5 scale; coverage is used only for ranking priority.
    all_candidates = set().union(*per_user_scores)
    combined: list[tuple[int, float, int]] = []  # (film_id, avg_score, n_scored)
    n = len(users)
    for fid in all_candidates:
        member_scores = [s[fid] for s in per_user_scores if fid in s]
        avg = sum(member_scores) / len(member_scores)
        combined.append((fid, avg, len(member_scores)))

    # Primary sort: number of members with a score (desc); secondary: avg score (desc)
    combined.sort(key=lambda x: (x[2], x[1]), reverse=True)
    results = _enrich(session, [(fid, avg) for fid, avg, _ in combined[:top_n]], min_tmdb_rating)
    if len(results) < top_n:
        already = {r["film_id"] for r in results}
        remaining = candidate_film_ids - seen_combined - already
        blended = _blend_affinity_and_semantic(session, usernames, remaining, user_means)
        results += _enrich(session, blended[:top_n - len(results)], min_tmdb_rating)
    if len(results) < top_n:
        already = {r["film_id"] for r in results}
        results += cold_start_recommendations(
            session, genre_ids, exclude_genre_ids or [], seen_combined | already, top_n - len(results), min_tmdb_rating
        )
    return results


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
    exclude_genre_ids: list[int],
    min_tmdb_rating: float,
    top_n: int,
) -> list[dict]:
    user = session.exec(select(LBUser).where(LBUser.username == username)).first()
    if not user:
        return []

    seen_film_ids = _expand_seen_by_tmdb_id(session, {
        r.film_id for r in session.exec(
            select(UserFilmRating).where(UserFilmRating.user_id == user.id)
        ).all()
    })
    seen_film_ids |= _load_vetoed_film_ids(session)

    rated_count = sum(1 for r in session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user.id, UserFilmRating.rating != None
        )
    ).all())

    candidate_film_ids = get_films_by_genres(session, genre_ids, exclude_genre_ids)
    candidate_film_ids -= seen_film_ids

    if rated_count < settings.cf_cold_start_threshold or not candidate_film_ids:
        return _affinity_then_cold_start(session, [username], genre_ids, exclude_genre_ids, candidate_film_ids, seen_film_ids, top_n, min_tmdb_rating)

    ratings_flat = _load_all_ratings(session)
    if not ratings_flat:
        return _affinity_then_cold_start(session, [username], genre_ids, exclude_genre_ids, candidate_film_ids, seen_film_ids, top_n, min_tmdb_rating)

    matrix, usernames, film_ids, user_means = build_sparse_matrix(ratings_flat)
    similar = find_similar_users(username, matrix, usernames, top_k=50)
    if not similar:
        return _affinity_then_cold_start(session, [username], genre_ids, exclude_genre_ids, candidate_film_ids, seen_film_ids, top_n, min_tmdb_rating)

    scored = score_unseen_films(
        target_username=username,
        similar_users=similar,
        matrix=matrix,
        usernames=usernames,
        film_ids=film_ids,
        seen_film_ids=seen_film_ids,
        candidate_film_ids=candidate_film_ids,
        user_means=user_means,
    )
    results = _enrich(session, scored[:top_n], min_tmdb_rating)
    if len(results) < top_n:
        already = {r["film_id"] for r in results}
        remaining = candidate_film_ids - seen_film_ids - already
        blended = _blend_affinity_and_semantic(session, [username], remaining, user_means)
        results += _enrich(session, blended[:top_n - len(results)], min_tmdb_rating)
    if len(results) < top_n:
        already = {r["film_id"] for r in results}
        results += cold_start_recommendations(
            session, genre_ids, exclude_genre_ids, seen_film_ids | already, top_n - len(results), min_tmdb_rating
        )
    return results


def _load_all_ratings(session: Session) -> list[dict]:
    all_raw = session.exec(
        select(UserFilmRating, LBUser).join(LBUser, LBUser.id == UserFilmRating.user_id)
    ).all()
    return [
        {"username": u.username, "film_id": r.film_id, "rating": r.rating}
        for r, u in all_raw
        if r.rating is not None and not u.is_audience_user
    ]


def _affinity_then_cold_start(
    session: Session,
    usernames: list[str],
    genre_ids: list[int],
    exclude_genre_ids: list[int],
    candidate_film_ids: set[int],
    seen_film_ids: set[int],
    top_n: int,
    min_tmdb_rating: float,
    user_means: dict[str, float] | None = None,
) -> list[dict]:
    """
    Score candidates by affinity (genre+keyword), optionally blended with
    semantic similarity when embeddings are ready, then pad with cold-start.
    """
    results: list[dict] = []
    if candidate_film_ids:
        scored = _blend_affinity_and_semantic(
            session, usernames, candidate_film_ids, user_means or {}
        )
        results = _enrich(session, scored[:top_n], min_tmdb_rating)
    if len(results) < top_n:
        already = {r["film_id"] for r in results}
        results += cold_start_recommendations(
            session, genre_ids, exclude_genre_ids, seen_film_ids | already, top_n - len(results), min_tmdb_rating
        )
    return results


def _blend_affinity_and_semantic(
    session: Session,
    usernames: list[str],
    candidate_film_ids: set[int],
    user_means: dict[str, float],
) -> list[tuple[int, float]]:
    """
    Blend affinity and semantic scores into a single ranked list.

    When semantic matching is enabled:
      final = 0.45 * affinity_score + 0.55 * semantic_score
    When only affinity is available:
      final = affinity_score
    Films with no affinity or semantic score fall back to their TMDB rating.
    """
    affinity_scores = dict(score_candidates_by_affinity(session, usernames, candidate_film_ids))

    if not semantic_matching_enabled(session):
        return sorted(affinity_scores.items(), key=lambda x: x[1], reverse=True)

    semantic_scores = dict(
        score_candidates_by_embedding(session, usernames, candidate_film_ids, user_means)
    )

    blended: list[tuple[int, float]] = []
    for fid in candidate_film_ids:
        a = affinity_scores.get(fid)
        s = semantic_scores.get(fid)
        if a is not None and s is not None:
            blended.append((fid, 0.45 * a + 0.55 * s))
        elif a is not None:
            blended.append((fid, a))
        elif s is not None:
            blended.append((fid, s))

    return sorted(blended, key=lambda x: x[1], reverse=True)


def _enrich(
    session: Session,
    scored: list[tuple[int, float]],
    min_tmdb_rating: float = 0.0,
) -> list[dict]:
    results = []
    for film_id, score in scored:
        film = session.get(Film, film_id)
        if not film:
            continue
        if min_tmdb_rating > 0 and film.tmdb_rating is not None and film.tmdb_rating < min_tmdb_rating:
            continue
        results.append(_film_to_dict(film, score))
    return results
