# Letterboxd Recommender

A self-hosted web app that recommends films based on your Letterboxd watch history. Works great for couples or small groups — it combines everyone's ratings, excludes anything anyone has already seen, and finds films you'd all enjoy.

<img width="1915" height="911" alt="image" src="https://github.com/user-attachments/assets/bac3852d-2f91-44cf-a5eb-6a9e835c07f5" />



## Features

- **Multi-user support** — add as many Letterboxd profiles as you like; recommendations are scored across the whole group
- **Full history import** — uploads your complete watch history via Letterboxd's data export (ratings + watched-but-unrated films)
- **Automatic updates** — new diary entries sync every 6 hours via each user's public RSS feed, no manual action needed
- **Already-seen exclusion** — films watched by *anyone* in the group (rated or not) are excluded from recommendations
- **Genre mood filter** — pick a genre (or several) before asking for recommendations
- **Collaborative filtering** — mean-centered cosine similarity on your ratings matrix, augmented with TMDB recommendation signals
- **Semantic matching** — optional AI embedding model reads each film's plot and finds thematic throughlines across genres (e.g. "moral ambiguity under pressure" across war, crime, and drama)
- **Affinity scoring** — scores candidates by genre, thematic keyword, director, and cast, blended by signal strength; all signals are temporally weighted so recent ratings count more than old ones
- **Veto system** — permanently exclude any film from recommendations with a 6-second undo window
- **Self-hosted** — your data never leaves your server; runs entirely in Docker

## Requirements

- Docker & Docker Compose
- A free [TMDB API key](https://www.themoviedb.org/settings/api)
- A Letterboxd account (free tier is fine)

## Quick start

```bash
git clone https://github.com/chanfriendly/letterboxd-recommender.git
cd letterboxd-recommender
cp .env.example .env
# Edit .env and add your TMDB_API_KEY
docker compose up -d
```

Then open `http://localhost:8020` in your browser.

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```env
TMDB_API_KEY=your_key_here
REDIS_URL=redis://redis:6379/0
DATABASE_URL=sqlite:///./data/letterboxd_rec.db
```

## Importing your watch history

Letterboxd does not offer a public API for reading watch history, so you need to export your data manually — once. After that, new entries are picked up automatically via RSS.

<img width="635" height="910" alt="image" src="https://github.com/user-attachments/assets/dab1c7d0-65d3-4fb0-8b8a-f130138a7cc6" />


1. Log in to Letterboxd and go to **letterboxd.com/settings/data/**
2. Click **Export Your Data** — Letterboxd will email you a download link
3. Download the `.zip` file (do not unzip it)
4. Open the app's **Setup** page, enter your username, and upload the `.zip`
5. The app processes the ZIP in the background and enriches every film with TMDB metadata; this takes a few minutes for large collections

Repeat for each person in your group.

## How it works

```
Letterboxd export ZIP
        │
        ▼
  parse ratings.csv          ┐
  parse watched.csv          │  extract slug from Letterboxd URI
        │                    ┘
        ▼
  TMDB metadata lookup  ──── genres, poster, overview, rating
        │
        ▼
  SQLite (SQLModel)  ──── films, user ratings, genre links
        │
        ▼
  Collaborative filtering  ── cosine similarity on ratings matrix
  TMDB rec signals         ── highly-rated films seed TMDB /recommendations
        │
        ▼
  Group scoring            ── average predicted score across all users
  Seen filter              ── exclude anything watched by anyone
        │
        ▼
  Recommendations page     ── top-N films with posters and match scores
```

**Scheduled refresh (every 6 h):** Each profile's public RSS feed (`letterboxd.com/username/rss/`) is polled for new diary entries and merged into the database.

## Tech stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Jinja2 |
| Frontend | Tailwind CSS (CDN) + vanilla JS |
| Database | SQLite via SQLModel |
| Task queue | Celery + Redis |
| Recommendations | scikit-learn (cosine similarity) + scipy sparse matrices |
| Semantic embeddings | sentence-transformers or any OpenAI-compatible API (LM Studio, Ollama) |
| Film metadata | TMDB API |
| Container | Docker Compose (web, worker, beat, redis) |

## Self-hosting notes

The `data/` directory (SQLite DB + temporary upload files) is volume-mounted and excluded from git. Back it up if you care about your import history.

The Docker image is based on `mcr.microsoft.com/playwright/python` (~2 GB) — Playwright is included for potential future use but is not required for the current CSV-based import flow.

To expose the app externally, put it behind a reverse proxy (nginx, Traefik) or use Tailscale Funnel:

```bash
docker exec tailscale tailscale funnel --bg 8020
```

## Semantic matching setup

<img width="626" height="694" alt="image" src="https://github.com/user-attachments/assets/b13da02a-77cf-4795-9beb-4f2c9aa8411f" />


Semantic matching is optional and off by default. To enable it, go to the **Setup** page and scroll to "Deep Semantic Matching."

**Local model** (default): the app downloads `all-MiniLM-L6-v2` (~80 MB) into the container on first use. No extra configuration needed.

**Remote API**: point to any OpenAI-compatible embeddings endpoint — LM Studio, Ollama, or OpenAI. Enter the base URL and model name on the Setup page and click "Test Connection" before saving. Works well with `nomic-embed-text` on Ollama.

The first run embeds every film in your library with an overview (~3,000–4,000 films typically). New films are embedded automatically after each sync. Switching embedding models requires clearing and re-embedding via the "Clear all embeddings" link on the Setup page.

## Known issues / next session

- **Already-seen films appearing in results (existing installs)** — Letterboxd diary entries sometimes use short-code slugs (e.g. `2DjO`) instead of canonical film slugs. Older versions of the import pipeline stored the short code as the film title, so TMDB lookup failed and these records were left as stubs with no `tmdb_id`. The deduplication step (`_expand_seen_by_tmdb_id`) can't bridge a stub with no TMDB ID to its counterpart in the recommendation pool. **Fix:** the import pipeline now carries the real `Name`/`Year` from the CSV through to TMDB lookup. Re-uploading your Letterboxd export ZIP will resolve existing stubs automatically — the import detects stub records (title == slug) and re-attempts enrichment with the real title from the CSV.

## Algorithm improvement ideas

### Better group recommendations
- **Least-misery scoring** — for couples, the bottleneck is the person who'd enjoy it least. A hybrid of `(avg + min) / 2` would surface films both people would genuinely enjoy over films one person loves and the other tolerates.

### Better CF scoring
- **Item-based CF** — instead of finding similar *users*, find similar *films*. More stable with a sparse user base because the item-item matrix accumulates signal across all users.
- **SVD / matrix factorization** — decompose the ratings matrix into latent taste dimensions. Works well with sparse data and generalises better than nearest-neighbor CF.

### Candidate pool
- **Expand seed depth dynamically** — if the candidate pool after genre filtering is thin, automatically fetch more TMDB recommendation pages.
- **Letterboxd Popular lists as signals** — seed candidates from Letterboxd's public genre charts and Top 250 to surface critically loved films TMDB misses.
- **Diversity pass** — re-rank results to penalise the same director or franchise appearing back-to-back.

<img width="232" height="25" alt="image" src="https://github.com/user-attachments/assets/03887ebc-1a7b-44dd-81fd-eacb96f15ca2" />


## License

MIT
