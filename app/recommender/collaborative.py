"""
Collaborative filtering via mean-centered cosine similarity on a sparse user-item matrix.

Mean-centering subtracts each user's average rating before computing similarity,
so the similarity reflects *taste direction* (what kinds of films a user prefers
relative to their own baseline) rather than rating habits (whether someone rates
everything 4 stars vs. 3 stars).  The target user's mean is added back when
producing the final predicted score, keeping results on the 0–5 scale.
"""

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.metrics.pairwise import cosine_similarity


def build_sparse_matrix(
    ratings: list[dict],
) -> tuple[csr_matrix, list[str], list[int], dict[str, float]]:
    """
    Build a mean-centered sparse user-item matrix from a flat list of rating dicts.

    Args:
        ratings: List of {username, film_id, rating}

    Returns:
        (sparse_matrix, ordered_usernames, ordered_film_ids, user_means)
        user_means maps username -> mean rating (for adding back at prediction time)
    """
    usernames = sorted({r["username"] for r in ratings})
    film_ids = sorted({r["film_id"] for r in ratings})

    user_idx = {u: i for i, u in enumerate(usernames)}
    film_idx = {f: i for i, f in enumerate(film_ids)}

    # Compute per-user mean ratings
    from collections import defaultdict
    user_rating_sums: dict[str, list[float]] = defaultdict(list)
    for r in ratings:
        if r["rating"] is not None:
            user_rating_sums[r["username"]].append(float(r["rating"]))
    user_means: dict[str, float] = {
        u: sum(vals) / len(vals) for u, vals in user_rating_sums.items()
    }

    # Build matrix with mean-centered values
    rows, cols, data = [], [], []
    for r in ratings:
        if r["rating"] is not None:
            mean = user_means.get(r["username"], 0.0)
            centered = float(r["rating"]) - mean
            if centered != 0.0:  # skip true-neutral entries to keep matrix sparse
                rows.append(user_idx[r["username"]])
                cols.append(film_idx[r["film_id"]])
                data.append(centered)

    matrix = csr_matrix(
        (data, (rows, cols)),
        shape=(len(usernames), len(film_ids)),
    )
    return matrix, usernames, film_ids, user_means


def find_similar_users(
    target_username: str,
    matrix: csr_matrix,
    usernames: list[str],
    top_k: int = 50,
) -> list[tuple[str, float]]:
    """
    Return top-K users most similar to target_username by cosine similarity
    on the mean-centered matrix.

    Returns list of (username, similarity_score).
    """
    if target_username not in usernames:
        return []

    user_idx = {u: i for i, u in enumerate(usernames)}
    target_idx = user_idx[target_username]
    target_vec = matrix[target_idx]

    sims = cosine_similarity(target_vec, matrix).flatten()
    sims[target_idx] = -1  # exclude self

    top_indices = np.argsort(sims)[::-1][:top_k]
    return [(usernames[i], float(sims[i])) for i in top_indices if sims[i] > 0]


def score_unseen_films(
    target_username: str,
    similar_users: list[tuple[str, float]],
    matrix: csr_matrix,
    usernames: list[str],
    film_ids: list[int],
    seen_film_ids: set[int],
    candidate_film_ids: set[int],
    user_means: dict[str, float],
) -> list[tuple[int, float]]:
    """
    Score unseen films using mean-centered weighted average ratings from similar users.

    Predicted score = target_mean + weighted_avg(similar_user_deviations)
    This keeps the output on the 0–5 scale while reflecting personalised taste direction.

    Returns list of (film_id, predicted_score) sorted descending.
    """
    user_idx = {u: i for i, u in enumerate(usernames)}
    target_mean = user_means.get(target_username, 3.0)

    # Track (weighted_deviation_sum, sim_sum) per film
    scores: dict[int, tuple[float, float]] = {}
    for username, sim_score in similar_users:
        if username not in user_idx:
            continue
        row = matrix[user_idx[username]]
        _, cols = row.nonzero()
        for col in cols:
            fid = film_ids[col]
            if fid in seen_film_ids or fid not in candidate_film_ids:
                continue
            # row values are already mean-centered deviations
            deviation = float(row[0, col])
            w_sum, s_sum = scores.get(fid, (0.0, 0.0))
            scores[fid] = (w_sum + deviation * sim_score, s_sum + sim_score)

    results = []
    for fid, (w_sum, s_sum) in scores.items():
        if s_sum <= 0:
            continue
        predicted = target_mean + (w_sum / s_sum)
        # Clamp to valid rating range
        predicted = max(0.5, min(5.0, predicted))
        results.append((fid, predicted))

    return sorted(results, key=lambda x: x[1], reverse=True)
