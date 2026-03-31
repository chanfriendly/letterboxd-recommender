# Letterboxd Recommender

A self-hosted web app that recommends films based on your Letterboxd watch history. Works great for couples or small groups — it combines everyone's ratings, excludes anything anyone has already seen, and finds films you'd all enjoy.

<img width="745" height="947" alt="image" src="https://github.com/user-attachments/assets/05562148-7ecc-4efb-9701-a6c2731995fe" />


## Features

- **Multi-user support** — add as many Letterboxd profiles as you like; recommendations are scored across the whole group
- **Full history import** — uploads your complete watch history via Letterboxd's data export (ratings + watched-but-unrated films)
- **Automatic updates** — new diary entries sync every 6 hours via each user's public RSS feed, no manual action needed
- **Already-seen exclusion** — films watched by *anyone* in the group (rated or not) are excluded from recommendations
- **Genre mood filter** — pick a genre (or several) before asking for recommendations
- **Collaborative filtering** — predicts scores using cosine-similarity on your ratings matrix, augmented with TMDB recommendation signals
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
| Film metadata | TMDB API |
| Container | Docker Compose (web, worker, beat, redis) |

## Self-hosting notes

The `data/` directory (SQLite DB + temporary upload files) is volume-mounted and excluded from git. Back it up if you care about your import history.

The Docker image is based on `mcr.microsoft.com/playwright/python` (~2 GB) — Playwright is included for potential future use but is not required for the current CSV-based import flow.

To expose the app externally, put it behind a reverse proxy (nginx, Traefik) or use Tailscale Funnel:

```bash
docker exec tailscale tailscale funnel --bg 8020
```

## Known issues / next session

- **Movie links** — clicking a film poster opens the wrong Letterboxd URL in some cases (likely films imported via TMDB recommendations that have a synthetic `tmdb-{id}` slug rather than a real Letterboxd slug). Need to fall back to `https://www.themoviedb.org/movie/{tmdb_id}` when the slug is synthetic.
- **Already-seen films occasionally appearing** — a small number of watched films slip through the seen-exclusion filter. Likely affects films whose Letterboxd slug in the ZIP doesn't match the slug stored from a TMDB-seeded recommendation. Needs a dedup pass keyed on `tmdb_id` rather than just `film.id`.
- **Limited recommendation results** — result sets are sometimes smaller than expected, especially for niche genre combinations. Investigate whether the candidate pool is being over-filtered before CF scoring, and consider relaxing the cold-start threshold or expanding TMDB recommendation seed depth.

## License

MIT
