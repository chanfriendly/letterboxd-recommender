# Development Process

This document captures the working process for this project so sessions can resume
without re-explanation. Read this at the start of any new session.

---

## Architecture at a glance

| Layer | Where |
|---|---|
| Web + API | FastAPI (`app/main.py`, `app/routers/`) |
| Background jobs | Celery workers (`app/tasks/`) |
| Recommendation logic | `app/recommender/` |
| DB models | `app/models/` — SQLModel / SQLite |
| Templates | `app/templates/` — Jinja2 + Tailwind CDN |
| Runtime | Docker Compose on TrueNAS NAS (`nas` / `100.127.164.49`) |

Docker services: **web**, **worker**, **beat**, **redis**
DB file: volume-mounted at `/app/data/letterboxd_rec.db` inside the container

---

## Dev workflow (while testing, before GitHub approval)

Code lives on this Mac at `~/github/letterboxd-recommender`.
The running app is on the NAS at `/mnt/volume1/apps/letterboxd-recommender`.

**1. Edit files locally on the Mac.**

**2. Push changed files directly to the NAS with rsync:**

```bash
# Single file
rsync -av -e "ssh -i ~/.ssh/id_ed25519 -p 22" \
  ~/github/letterboxd-recommender/app/recommender/pipeline.py \
  truenas_admin@100.127.164.49:/mnt/volume1/apps/letterboxd-recommender/app/recommender/

# Multiple files to same directory
rsync -av -e "ssh -i ~/.ssh/id_ed25519 -p 22" \
  ~/github/letterboxd-recommender/app/recommender/pipeline.py \
  ~/github/letterboxd-recommender/app/recommender/affinity.py \
  truenas_admin@100.127.164.49:/mnt/volume1/apps/letterboxd-recommender/app/recommender/

# Templates
rsync -av -e "ssh -i ~/.ssh/id_ed25519 -p 22" \
  ~/github/letterboxd-recommender/app/templates/index.html \
  truenas_admin@100.127.164.49:/mnt/volume1/apps/letterboxd-recommender/app/templates/
```

**Watch the destination directory:** always match the source file's path under `app/`.
A common mistake is rsync-ing a file from `app/tasks/` into `app/recommender/` — double-check the trailing path.

**3. Restart the containers to pick up changes:**

```bash
ssh -i ~/.ssh/id_ed25519 -p 22 truenas_admin@100.127.164.49 \
  "cd /mnt/volume1/apps/letterboxd-recommender && docker compose restart web worker beat"
```

**4. Test on the Tailscale URL.** Confirm the change works before committing.

**5. Check logs if something breaks:**

```bash
ssh -i ~/.ssh/id_ed25519 -p 22 truenas_admin@100.127.164.49 \
  "cd /mnt/volume1/apps/letterboxd-recommender && docker compose logs web --tail 50"

# Worker logs (Celery tasks, background jobs):
ssh -i ~/.ssh/id_ed25519 -p 22 truenas_admin@100.127.164.49 \
  "cd /mnt/volume1/apps/letterboxd-recommender && docker compose logs worker --tail 50"
```

---

## GitHub workflow (once changes are approved)

```bash
cd ~/github/letterboxd-recommender

# Stage only the files you changed
git add app/recommender/pipeline.py app/templates/index.html  # etc.

# Check for remote changes first (e.g. README edits via GitHub web UI)
git pull --rebase origin main

# Commit with a descriptive message
git commit -m "Brief summary of what changed and why

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

git push origin main
```

---

## What to update when making each type of change

| Change type | Files to update |
|---|---|
| New DB table | `app/models/film.py` (or relevant model file) + `app/main.py` (import for registration) |
| New API endpoint | `app/routers/api.py` |
| New UI page | `app/routers/ui.py` (route) + `app/templates/<name>.html` + `app/templates/base.html` (nav link) |
| New background job | `app/tasks/scrape_user.py` + `app/routers/api.py` (trigger endpoint if needed) |
| Recommendation logic | `app/recommender/` — `pipeline.py` is the orchestrator; CF is in `collaborative.py`; affinity/keyword scoring in `affinity.py`; fallback in `fallback.py` |
| New dependency | `requirements.txt` — then rebuild the Docker image (`docker compose build`) |
| Config/settings | `app/config.py` — add field with default; set in `.env` |
| Algorithm explanation | `app/templates/methodology.html` — keep in sync with actual logic |
| Known issues / next session notes | `README.md` — "Known issues / next session" section |

---

## Adding a column to an existing model

`SQLModel.metadata.create_all()` creates *new* tables automatically on startup,
but **never adds columns to existing tables**. If you add a field to an existing
model (e.g. `Film`, `LBUser`), you must migrate the live DB manually.

The sqlite3 CLI is not in the container image — use Python via `docker exec`:

```bash
ssh -i ~/.ssh/id_ed25519 -p 22 truenas_admin@100.127.164.49 \
  "docker exec letterboxd-recommender-web-1 python -c \"
import sqlite3
conn = sqlite3.connect('/app/data/letterboxd_rec.db')
conn.execute('ALTER TABLE <table> ADD COLUMN <name> <type>')
conn.commit()
conn.close()
print('done')
\""
```

Then restart the containers. Forgetting this step causes `OperationalError: no such column` at runtime.

New *tables* (entirely new SQLModel classes) are safe — `create_all` handles those.

---

## Adding a new dependency

Unlike pure code changes (which just need rsync + restart), new Python packages
require a Docker image rebuild:

```bash
# 1. Add to requirements.txt locally
echo "sentence-transformers" >> requirements.txt

# 2. Rebuild and restart on the NAS
ssh -i ~/.ssh/id_ed25519 -p 22 truenas_admin@100.127.164.49 \
  "cd /mnt/volume1/apps/letterboxd-recommender && docker compose build && docker compose up -d"
```

---

## Scoring pipeline (how recommendations are produced)

Three-tier fallback chain. Each tier fills gaps left by the one above:

```
1. Collaborative filtering (collaborative.py)
   Mean-centered cosine similarity → score_unseen_films()
   Requires: ≥20 rated films per user, overlap with other users in DB
   Output: predicted rating on 0–5 scale

2. Affinity scoring (affinity.py)
   Genre affinity (40%) + keyword affinity (60%)
   score_candidates_by_affinity()
   Requires: user's own rated history only
   Output: 0–5 compatibility score

3. Cold-start fallback (fallback.py)
   TMDB audience average rating — no personalisation
   Shown as "TMDB: X.X" in UI (not "Match") to signal this

4. [Optional, opt-in] Semantic embeddings
   sentence-transformers all-MiniLM-L6-v2
   Input format: "{title}. {overview}" — same at index and query time
   Enable via Setup page toggle
```

Group scoring: each user scored independently → sort by (n_users_scored DESC, avg_score DESC).

---

## Key design decisions (with rationale)

- **`__tmdb_recs__` excluded from CF matrix** — it's a synthetic user with uniform implied ratings that flattens all CF scores. It expands the candidate pool but must not participate in similarity computation.
- **Mean-centered ratings in CF** — subtract each user's mean before cosine similarity so taste direction matters more than rating habits (someone who gives everything 4 stars looks different from someone who genuinely loves what they rate 4).
- **Keywords weighted 60%, genres 40%** — keywords are more semantically specific and better at cross-genre thematic matching. Genre acts as a broad prior when keyword data is thin.
- **Vetoes are group-wide** — one person vetoing a film removes it for everyone, which matches the shared-viewing context.
- **Embedding input format** — `"{title}. {overview}"` used consistently at both index and query time. This is important: embedding space coherence requires the same format everywhere.

---

## Session handoff checklist

At the end of a session:
- [ ] Tested changes on the Tailscale URL
- [ ] Committed and pushed to GitHub
- [ ] Updated `README.md` "Known issues / next session" if anything is unresolved
- [ ] Updated `app/templates/methodology.html` if the scoring logic changed
