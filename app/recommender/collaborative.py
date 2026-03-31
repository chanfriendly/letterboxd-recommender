"""
Collaborative filtering via cosine similarity on a sparse user-item ratings matrix.
"""

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.metrics.pairwise import cosine_similarity


def build_sparse_matrix(
    ratings: list[dict],
) -> tuple[csr_matrix, list[str], list[int]]:
    """
    Build a sparse user-item matrix from a flat list of rating dicts.

    Args:
        ratings: List of {username, film_id, rating}

    Returns:
        (sparse_matrix, ordered_usernames, ordered_film_ids)
    """
    usernames = sorted({r["username"] for r in ratings})
    film_ids = sorted({r["film_id"] for r in ratings})

    user_idx = {u: i for i, u in enumerate(usernames)}
    film_idx = {f: i for i, f in enumerate(film_ids)}

    rows, cols, data = [], [], []
    for r in ratings:
        if r["rating"] is not None:
            rows.append(user_idx[r["username"]])
            cols.append(film_idx[r["film_id"]])
            data.append(float(r["rating"]))

    matrix = csr_matrix(
        (data, (rows, cols)),
        shape=(len(usernames), len(film_ids)),
    )
    return matrix, usernames, film_ids


def find_similar_users(
    target_username: str,
    matrix: csr_matrix,
    usernames: list[str],
    top_k: int = 50,
) -> list[tuple[str, float]]:
    """
    Return top-K users most similar to target_username by cosine similarity.
    Returns list of (username, similarity_score).
    """
    if target_username not in usernames:
        return []

    user_idx = {u: i for i, u in enumerate(usernames)}
    target_idx = user_idx[target_username]
    target_vec = matrix[target_idx]

    # Compute similarity between target and all other users
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
) -> list[tuple[int, float]]:
    """
    Score unseen films using weighted average ratings from similar users.

    Returns list of (film_id, predicted_score) sorted descending.
    """
    user_idx = {u: i for i, u in enumerate(usernames)}
    film_idx = {f: i for i, f in enumerate(film_ids)}

    scores: dict[int, list[float]] = {}
    for username, sim_score in similar_users:
        if username not in user_idx:
            continue
        row = matrix[user_idx[username]]
        _, cols = row.nonzero()
        for col in cols:
            fid = film_ids[col]
            if fid in seen_film_ids or fid not in candidate_film_ids:
                continue
            rating = row[0, col] * sim_score
            scores.setdefault(fid, []).append(rating)

    results = [
        (fid, float(np.mean(weighted_ratings)))
        for fid, weighted_ratings in scores.items()
    ]
    return sorted(results, key=lambda x: x[1], reverse=True)
