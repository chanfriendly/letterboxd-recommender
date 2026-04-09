# CHANGELOG — Letterboxd Recommender

> This file is the session memory for this project. Every session should begin by reading it and end by updating it. Document what worked, what didn't, and why. Without failed approaches recorded here, future sessions will repeat the same dead ends.

---

## Current Status

**Phase:** Feature-complete MVP / refinement  
**Last updated:** 2026-04-09  
**Active focus:** Scoring improvements shipped — temporal weighting + director/cast affinity live.

---

## Completed Work

### Session 001 — Initial build
- Initial commit: FastAPI + Jinja2 web app, Celery + Redis task queue, SQLite via SQLModel
- Basic Letterboxd diary import, TMDB metadata enrichment, recommendation page

### Session 002 — CSV import, multi-user, docs
- Switched from scraper-based to CSV import flow (Letterboxd export ZIP)
- Added multi-user support (multiple Letterboxd profiles, group scoring)
- Documented project in `DEVELOPMENT.md`

### Session 003 — Filters, veto, methodology
- Added genre exclusion filter and minimum TMDB score filter
- Added veto system (permanent film exclusion with 6-second undo)
- Added `app/templates/methodology.html` — explains scoring pipeline to users
- Fixed scoring bugs

### Session 004 — Keyword affinity, mean-centered CF, semantic scaffold
- Added keyword affinity scoring (TMDB thematic keywords)
- Implemented mean-centered cosine similarity for collaborative filtering
- Added scaffolding for semantic matching (embeddings not yet live)
- Documented scoring pipeline and design decisions in `DEVELOPMENT.md`

### Session 005 — Semantic embeddings live
- Integrated sentence-transformers and remote embedding API support
- Full embedding pipeline: compute taste vector from above-mean rated films, cosine similarity against candidate pool
- Added Setup page for configuring embedding provider (local or remote OpenAI-compatible)
- Documented `ALTER TABLE` migration process in `DEVELOPMENT.md` (SQLModel `create_all` limitation)

### Session 006 — Configurable embedding provider, status endpoint
- Added configurable remote embedding provider (Jetson Ollama `nomic-embed-text` 768-dim)
- Status endpoint counts only films with TMDB overviews as `films_total` (100% = all embeddable films done)
- New films auto-embedded after each sync when `semantic_matching_ready=true`

### Session 008 — Scoring improvements: temporal weighting + director/cast affinity
- **Temporal weighting:** All affinity signals (genre, keyword, director, cast) and the semantic taste vector now apply exponential decay with an 18-month half-life. Recent ratings carry more weight than old ones. Films without a `watched_at` date get full weight (backward-compatible). `watched_at` is now populated from the Letterboxd ZIP export `Date` column and RSS `watched_date`.
- **Director/cast affinity:** New `FilmPerson` and `FilmPersonLink` tables. TMDB `/credits` fetched for every film (director + top 3 cast). Affinity blend updated: 30% genre + 45% keyword + 15% director + 10% cast. Directors carry 2× weight of cast within the people signal.
- Backfill note: existing films will get credits populated incrementally on the next sync/enrichment pass. No manual migration needed — new tables were created automatically by `create_all`.

### Session 007 — Bug fixes
- Fixed genre exclude filter not applied in cold-start fallback
- Fixed stub film resolution: `_persist_films` now detects Letterboxd short-code slugs (title == slug, tmdb_id None) and re-enriches them from the real Name/Year in the CSV. Users must re-upload their ZIP to fix existing stubs.

---

## Failed Approaches

*What was tried, what happened, and why it was abandoned. Read this before trying anything new.*

| Date | Approach | What Happened | Why Abandoned |
|---|---|---|---|
| Early | Playwright-based scraper for Letterboxd history | Scraper was fragile; Letterboxd HTML structure changed | Replaced with CSV import (official data export ZIP) |
| Early | Including `__tmdb_recs__` synthetic user in CF matrix | Synthetic user has uniform implied ratings — flattened all CF scores, homogenised results | Excluded via `is_audience_user=True` flag |

---

## Known Issues / Limitations

- **Already-seen films appearing in results (existing installs)** — caused by stub films with short-code slugs as titles and no `tmdb_id`. The deduplication step can't match them. Fix: re-upload the Letterboxd export ZIP — the import pipeline now resolves stubs automatically. New installs are not affected.
- **Genre exclude filter gaps** — filter was not applied in the cold-start fallback tier (fixed in Session 007), but edge cases may remain.
- **Sparse user base limits CF** — CF requires ≥20 rated films per user and overlap between users in the DB. Small groups with little rating overlap fall through to affinity/semantic tier.
- **662 films have no TMDB overview** — they cannot be embedded; this gap is expected and permanent unless TMDB adds overviews.

---

## Algorithm Improvement Ideas (Backlog)

### Group scoring
- **Least-misery scoring** — `(avg + min) / 2` to surface films both people enjoy rather than films one loves and the other tolerates

### CF improvements
- **Item-based CF** — find similar films instead of similar users; more stable with sparse user base
- **SVD / matrix factorization** — decompose ratings matrix into latent taste dimensions; generalises better than nearest-neighbor

### Candidate pool
- **Expand seed depth dynamically** — if genre-filtered pool is thin, fetch more TMDB recommendation pages
- **Letterboxd Popular lists as signals** — seed from public genre charts and Top 250
- **Diversity pass** — penalise same director or franchise appearing back-to-back in results

### Discovery / serendipity (future, needs design thought)
- **Serendipity tier** — a separate small section surfacing films that score low on affinity/semantic similarity but are loved by CF-similar users. Goal: genuine surprises that break pattern without being random. Requires UI change (second results section) and careful tuning of what counts as "surprising enough." Held for design discussion.

---

## Next Steps

1. Trigger a re-import or sync for existing users so director/cast credits get backfilled via `_enrich_with_tmdb`
2. Pick next algorithm improvement from the backlog (least-misery scoring for groups, or item-based CF)
3. Design the serendipity/contrast tier (held — needs more thought before implementation)
4. Investigate NAS load average (~13) — determine if this app is a contributor

---

## Design Decisions

| Date | Decision | Rationale |
|---|---|---|
| Early | Keywords weighted 60%, genres 40% in affinity score | Keywords are more semantically specific; genre is a broad prior when keyword data is thin |
| Early | Semantic taste vector uses only above-mean films | Rating below the user's average contributes nothing; vector should point toward love, not be dragged toward dislikes |
| Early | Embedding input format: `"{title}. {overview}"` | Must be consistent at both index and query time for vector space coherence |
| Early | Vetoes are group-wide | Shared-viewing context: one person's veto removes the film for everyone |
| Early | Jetson for embeddings, not Mac mini | Mac mini runs Qwen 3.5:9b daily; memory competition would hurt both. Jetson has dedicated GPU, runs Ollama always-on |
| Early | Mean-centered ratings in CF | Taste direction matters more than rating habits; normalises for users who give everything 4 stars vs. those who are generous with 5s |

---

## Session Log

### Session 001–007 — [dates not recorded]
**Summary:** Full project build from scratch through bug-fix phase. See Completed Work above.

### Session 008 — 2026-04-09
**Goal:** Add CLAUDE.md, CHANGELOG.md, push demo mode, review and improve scoring  
**Outcome:** Created docs. Pushed demo mode (was already live on NAS but untracked). Shipped temporal weighting + director/cast affinity. RSS confirmed working (3 entries in last 7 days for chanfriendly). Serendipity tier added to backlog for future design.  
**Next session should start with:** Trigger a sync to backfill credits for existing films, then pick from Next Steps.
