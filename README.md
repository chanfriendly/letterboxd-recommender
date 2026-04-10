# Letterboxd Recommender

A film recommendation app for couples and small groups. Import your Letterboxd watch histories, pick a mood, and get recommendations scored across everyone — filtered to films none of you have seen.

<img width="1915" height="911" alt="image" src="https://github.com/user-attachments/assets/bac3852d-2f91-44cf-a5eb-6a9e835c07f5" />

## Features

- **Multi-user support** — add as many Letterboxd profiles as you like; recommendations are scored across the whole group
- **Full history import** — uploads your complete watch history via Letterboxd's data export (ratings + watched-but-unrated films)
- **Automatic updates** — new diary entries sync every 6 hours via each user's public RSS feed, no manual action needed
- **Already-seen exclusion** — films watched by *anyone* in the group (rated or not) are excluded from recommendations
- **Genre mood filter** — pick a genre (or several) before asking for recommendations
- **Collaborative filtering** — mean-centered cosine similarity on your ratings matrix, augmented with TMDB recommendation signals
- **Semantic matching** — optional AI embedding model reads each film's plot and finds thematic throughlines across genres
- **Affinity scoring** — scores candidates by genre, thematic keyword, director, and cast; all signals are temporally weighted so recent ratings count more than old ones
- **Veto system** — permanently exclude any film from recommendations with a 6-second undo window
- **Self-hosted** — your data never leaves your machine

## Install

### Mac app *(coming soon)*

Download, drag to Applications, click the icon. No setup required — the app walks you through everything in-browser.

> The Mac app is in development. Star or watch this repo to be notified when it's available.

### Self-hosted with Docker

Requires Docker & Docker Compose.

```bash
git clone https://github.com/chanfriendly/letterboxd-recommender.git
cd letterboxd-recommender
cp .env.example .env
docker compose up -d
```

Open `http://localhost:8020` — a setup wizard walks you through connecting to TMDB and importing your first Letterboxd profile.

> **Optional:** Set `TMDB_API_KEY` in `.env` before starting to skip step 1 of the setup wizard.

## First-time setup

When you open the app, a setup wizard walks you through everything:

1. **Connect to TMDB** — the app asks for a free TMDB API key with step-by-step instructions and a direct link. Takes about 2 minutes. The key is saved inside the app — no config files to edit.
2. **Import your Letterboxd history** — the wizard explains how to export your data from Letterboxd and upload it. New diary entries sync automatically every 6 hours after that.

To add more people to your group, go to **Settings** after the initial setup.

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

## Known issues

- **Already-seen films appearing in results (existing installs)** — caused by stub films with short-code slugs as titles and no `tmdb_id`. Fix: re-upload your Letterboxd export ZIP — the import pipeline now resolves stubs automatically.

## License

MIT
