# CLAUDE.md — Letterboxd Recommender Development Guide

## Before Starting Any Session

1. **Read `CHANGELOG.md` first** — current status, what's in progress, what failed, next steps.
2. Read this file — confirm you understand the architecture and active design decisions.
3. Read `DEVELOPMENT.md` — rsync commands, DB migration process, and scoring pipeline details.
4. Check `AppSetting` table state before debugging any embedding issues.

---

## Project Overview

A self-hosted film recommendation web app for couples/groups. Users import their Letterboxd watch history; the app produces personalised recommendations using a three-tier scoring pipeline.

**Live instance:** NAS at `/mnt/volume1/apps/letterboxd-recommender`
**Local dev copy:** `~/GitHub/letterboxd-recommender`
**Accessible at:** Tailscale URL (see `.env` or ask the user)

---

## Architecture at a Glance

| Layer | Where |
|---|---|
| Web + API | FastAPI (`app/main.py`, `app/routers/`) |
| Background jobs | Celery workers (`app/tasks/`) |
| Recommendation logic | `app/recommender/` |
| DB models | `app/models/` — SQLModel / SQLite |
| Templates | `app/templates/` — Jinja2 + Tailwind CDN |
| Runtime | Docker Compose on TrueNAS (`nas` / `100.127.164.49`) |

Docker services: **web**, **worker**, **beat**, **redis**
DB file: volume-mounted at `/app/data/letterboxd_rec.db` inside the container

---

## Scoring Pipeline (Three-Tier Fallback)

```
1. Collaborative filtering (collaborative.py)
   Mean-centered cosine similarity → score_unseen_films()
   Requires: ≥20 rated films per user, overlap with other users in DB
   Output: predicted rating on 0–5 scale

2. Affinity + semantic blend (pipeline._blend_affinity_and_semantic)
   Genre affinity (40%) + keyword affinity (60%) → affinity score
   Semantic: taste vector vs candidate cosine sim
   When both available: final = 0.45 * affinity + 0.55 * semantic
   Requires: user's own rated history; semantic requires embeddings computed
   Output: 0–5 compatibility score

3. Cold-start fallback (fallback.py)
   TMDB audience average rating — no personalisation
   Shown as "TMDB: X.X" (not "Match") to signal this
```

Group scoring: each user scored independently → sort by (n_users_scored DESC, avg_score DESC).

---

## Dev Workflow

**Edit locally → rsync to NAS → restart containers → test → commit.**

See `DEVELOPMENT.md` for the full rsync and SSH commands.

Key rules:
- Always match the rsync destination path to the source file's path under `app/`. Misrouting `app/tasks/` into `app/recommender/` is a common mistake.
- New Python dependencies require `docker compose build`, not just restart.
- New DB columns require a manual `ALTER TABLE` via `docker exec python -c` — `create_all()` never adds columns to existing tables.
- Commit and push only after testing on the Tailscale URL.

---

## Semantic Embeddings (This Instance)

- **Provider:** Jetson Ollama at `http://100.117.49.22:11434/v1`, model `nomic-embed-text` (768-dim)
- **Configured in:** `AppSetting` table (`embedding_provider=remote`, `embedding_remote_url`, `embedding_remote_model`)
- **Input format:** `"{title}. {overview}"` — must be consistent at both index and query time
- **Status:** 3,410 films embedded; 662 have no TMDB overview and cannot be embedded (100% = all embeddable films done, not total films)
- **Taste vector:** weighted avg of above-mean film embeddings (weight = `rating - user_mean`, positive only)

Do not move embeddings to the Mac mini — it runs Qwen 3.5:9b daily and competing for unified memory is undesirable. The Jetson is dedicated ML hardware.

---

## Key Gotchas

- **`SQLModel.create_all` never adds columns** — always use `ALTER TABLE` via `docker exec python -c` with sqlite3 for existing tables.
- **`__tmdb_recs__` synthetic user** — must be excluded from CF matrix (`is_audience_user=True`). It's a synthetic user that expands the candidate pool but would flatten all CF scores if included.
- **Letterboxd short-code slugs** — diary entries sometimes use short-codes (e.g. `2DjO`) as film identifiers. Old imports stored these as the film title. `_persist_films` now detects stubs (title == slug, tmdb_id is None) and enriches them from the real CSV Name/Year. Users must re-upload their ZIP to fix existing stubs.
- **Switching embedding models** — incompatible vector spaces; must clear all embeddings first via Setup page.
- **`semantic_matching_ready=true` with `films_embedded < films_total`** is normal — the gap is films without TMDB overviews.
- **TMDB candidate films have no overviews initially** — `_enrich_with_tmdb` backfills them.

---

## What to Update for Each Change Type

| Change type | Files to update |
|---|---|
| New DB table | `app/models/<file>.py` + `app/main.py` (import for registration) |
| New API endpoint | `app/routers/api.py` |
| New UI page | `app/routers/ui.py` + `app/templates/<name>.html` + `app/templates/base.html` (nav link) |
| New background job | `app/tasks/scrape_user.py` + `app/routers/api.py` |
| Recommendation logic | `app/recommender/` — `pipeline.py` orchestrates; `collaborative.py` for CF; `affinity.py` for genre/keyword; `fallback.py` for cold-start |
| New dependency | `requirements.txt` → rebuild Docker image |
| Config/settings | `app/config.py` — add field with default; set in `.env` |
| Scoring logic change | `app/templates/methodology.html` — keep in sync |
| Session notes / issues | `CHANGELOG.md` — always update before ending a session |

---

## Commit Protocol

```bash
cd ~/GitHub/letterboxd-recommender
git add <specific files>
git pull --rebase origin main
git commit -m "Brief summary of what changed and why

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
git push origin main
```

Only commit after testing on the Tailscale URL.

---

## Session Handoff Checklist

At the end of every session:
- [ ] Tested changes on the Tailscale URL
- [ ] Committed and pushed to GitHub
- [ ] Updated `CHANGELOG.md` with what was done, what failed, and next steps
- [ ] Updated `app/templates/methodology.html` if scoring logic changed
