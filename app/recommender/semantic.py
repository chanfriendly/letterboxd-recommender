"""
Semantic similarity scoring via sentence-transformer embeddings.

Each film's overview is embedded as "{title}. {overview}" using all-MiniLM-L6-v2
(384-dim, ~80 MB, CPU-friendly).  Embeddings are stored in Film.embedding as a
JSON float list and computed once via the Setup page toggle.

A user's "taste vector" is the weighted average of embeddings of their rated
films, where the weight for each film is max(0, rating - user_mean).  Films
rated below the user's mean contribute nothing — we want the taste vector to
point toward what the user loves, not be dragged toward what they disliked.

Candidate films are then ranked by cosine similarity to this taste vector.
Similarities are min-max normalised within the candidate set so the output
uses the full 0–5 scale rather than clustering in a narrow band.

For a group, each member's taste vector is averaged before scoring (equivalent
to finding the centroid of the group's combined taste space).
"""

import json
import numpy as np
from sqlmodel import Session, select

from app.models.film import Film, AppSetting
from app.models.user import LBUser, UserFilmRating
from app.recommender.affinity import _temporal_weight


def semantic_matching_enabled(session: Session) -> bool:
    """Return True if embeddings have been computed and are ready to use."""
    setting = session.exec(
        select(AppSetting).where(AppSetting.key == "semantic_matching_ready")
    ).first()
    return setting is not None and setting.value == "true"


def _build_taste_vector(
    session: Session,
    user_id: int,
    user_mean: float,
) -> np.ndarray | None:
    """
    Weighted average of embeddings of films rated above the user's mean.
    Returns a unit-norm vector, or None if no embedded rated films exist.
    """
    rows = session.exec(
        select(UserFilmRating).where(
            UserFilmRating.user_id == user_id,
            UserFilmRating.rating != None,  # noqa: E711
        )
    ).all()

    vectors, weights = [], []
    for ufr in rows:
        film = session.get(Film, ufr.film_id)
        if not film or not film.embedding:
            continue
        deviation = ufr.rating - user_mean
        if deviation <= 0:
            continue  # ignore films rated at or below the user's average
        vectors.append(np.array(json.loads(film.embedding), dtype=np.float32))
        weights.append(deviation * _temporal_weight(ufr.watched_at))

    if not vectors:
        return None

    taste = np.average(np.stack(vectors), axis=0, weights=np.array(weights))
    norm = np.linalg.norm(taste)
    return taste / norm if norm > 0 else None


def score_candidates_by_embedding(
    session: Session,
    usernames: list[str],
    candidate_film_ids: set[int],
    user_means: dict[str, float],
) -> list[tuple[int, float]]:
    """
    Score candidate films by cosine similarity to the group's taste vector.

    Returns (film_id, score_0_to_5) sorted descending.
    Scores are min-max normalised within the candidate set.
    Returns an empty list if embeddings aren't ready or no taste vector can be built.
    """
    if not semantic_matching_enabled(session) or not candidate_film_ids:
        return []

    # Build per-user taste vectors and average them into a group vector
    taste_vectors = []
    for username in usernames:
        user = session.exec(select(LBUser).where(LBUser.username == username)).first()
        if not user:
            continue
        mean = user_means.get(username, 3.0)
        vec = _build_taste_vector(session, user.id, mean)
        if vec is not None:
            taste_vectors.append(vec)

    if not taste_vectors:
        return []

    group_taste = np.mean(np.stack(taste_vectors), axis=0)
    norm = np.linalg.norm(group_taste)
    if norm > 0:
        group_taste = group_taste / norm

    # Compute cosine similarity for each candidate that has an embedding
    raw_scores: list[tuple[int, float]] = []
    for film_id in candidate_film_ids:
        film = session.get(Film, film_id)
        if not film or not film.embedding:
            continue
        emb = np.array(json.loads(film.embedding), dtype=np.float32)
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        sim = float(np.dot(group_taste, emb))
        raw_scores.append((film_id, sim))

    if not raw_scores:
        return []

    # Min-max normalise similarities to [0.5, 5.0] so scores spread across
    # the visible range rather than clustering in a narrow cosine-sim band
    sims = np.array([s for _, s in raw_scores])
    s_min, s_max = sims.min(), sims.max()
    if s_max > s_min:
        normalised = 0.5 + (sims - s_min) / (s_max - s_min) * 4.5
    else:
        normalised = np.full_like(sims, 3.0)

    results = [(fid, float(score)) for (fid, _), score in zip(raw_scores, normalised)]
    return sorted(results, key=lambda x: x[1], reverse=True)
