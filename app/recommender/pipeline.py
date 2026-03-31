"""
Full recommendation pipeline: orchestrates CF, content filtering, and fallback.
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


def run_recommendations(
    session: Session,
    username: str,
    genre_ids: list[int],
    top_n: int = 20,
) -> list[dict]:
    """
    Main entry point for generating recommendations.

    1. Load user's ratings
    2. Check cold-start threshold
    3. If enough data: collaborative filtering + genre filter
    4. Otherwise: cold-start fallback
    """
    user = session.exec(select(LBUser).where(LBUser.username == username)).first()
    if not user:
        return []

    user_ratings = session.exec(
        select(UserFilmRating).where(UserFilmRating.user_id == user.id)
    ).all()

    seen_film_ids = {r.film_id for r in user_ratings}

    # Cold-start check
    rated_count = sum(1 for r in user_ratings if r.rating is not None)
    if rated_count < settings.cf_cold_start_threshold:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    # Genre-filtered candidate set
    candidate_film_ids = get_films_by_genres(session, genre_ids)
    candidate_film_ids -= seen_film_ids
    if not candidate_film_ids:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    # Load all ratings (target user + audience users) for CF
    all_ratings_raw = session.exec(
        select(UserFilmRating, LBUser)
        .join(LBUser, LBUser.id == UserFilmRating.user_id)
    ).all()

    ratings_flat = [
        {
            "username": lb_user.username,
            "film_id": ufr.film_id,
            "rating": ufr.rating,
        }
        for ufr, lb_user in all_ratings_raw
        if ufr.rating is not None
    ]

    if not ratings_flat:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    matrix, usernames, film_ids = build_sparse_matrix(ratings_flat)
    similar_users = find_similar_users(username, matrix, usernames, top_k=50)

    if not similar_users:
        return cold_start_recommendations(session, genre_ids, seen_film_ids, top_n)

    scored = score_unseen_films(
        target_username=username,
        similar_users=similar_users,
        matrix=matrix,
        usernames=usernames,
        film_ids=film_ids,
        seen_film_ids=seen_film_ids,
        candidate_film_ids=candidate_film_ids,
    )

    # Enrich top-N with film metadata
    results = []
    for film_id, score in scored[:top_n]:
        film = session.get(Film, film_id)
        if film:
            results.append(_film_to_dict(film, score))

    return results
