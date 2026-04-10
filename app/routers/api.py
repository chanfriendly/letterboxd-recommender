import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.models.db import get_session
from app.models.film import Film, VetoedFilm, AppSetting
from app.models.job import ScrapeJob
from app.models.profile import UserProfile
from app.models.user import LBUser, UserFilmRating
import httpx as _httpx

from app.tasks.scrape_user import (
    run_recommendation_job, refresh_all_profiles, process_zip_task,
    compute_embeddings_task, _get_app_setting, _set_app_setting, get_tmdb_api_key,
)

router = APIRouter(prefix="/api")

TMDB_GENRES = [
    {"id": 28, "name": "Action"},
    {"id": 12, "name": "Adventure"},
    {"id": 16, "name": "Animation"},
    {"id": 35, "name": "Comedy"},
    {"id": 80, "name": "Crime"},
    {"id": 99, "name": "Documentary"},
    {"id": 18, "name": "Drama"},
    {"id": 10751, "name": "Family"},
    {"id": 14, "name": "Fantasy"},
    {"id": 36, "name": "History"},
    {"id": 27, "name": "Horror"},
    {"id": 10402, "name": "Music"},
    {"id": 9648, "name": "Mystery"},
    {"id": 10749, "name": "Romance"},
    {"id": 878, "name": "Science Fiction"},
    {"id": 53, "name": "Thriller"},
    {"id": 10752, "name": "War"},
    {"id": 37, "name": "Western"},
]


class RecommendRequest(BaseModel):
    genre_ids: list[int] = []
    exclude_genre_ids: list[int] = []
    min_tmdb_rating: float = 0.0


class ProfileRequest(BaseModel):
    username: str
    display_name: str = ""


@router.get("/genres")
def list_genres():
    return TMDB_GENRES


# ---------------------------------------------------------------------------
# Setup wizard endpoints
# ---------------------------------------------------------------------------

class TmdbKeyRequest(BaseModel):
    api_key: str


@router.get("/setup/status")
def setup_status(session: Session = Depends(get_session)):
    """Return first-run setup state so the wizard knows which step to show."""
    has_key = bool(get_tmdb_api_key(session))
    profiles = session.exec(select(UserProfile).where(UserProfile.has_data == True)).all()  # noqa: E712
    return {"has_tmdb_key": has_key, "has_profiles": len(profiles) > 0}


@router.post("/setup/tmdb-key")
def save_tmdb_key(body: TmdbKeyRequest, session: Session = Depends(get_session)):
    """Validate a TMDB API key against the TMDB API, then persist it to AppSetting."""
    key = body.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty.")
    try:
        resp = _httpx.get(
            "https://api.themoviedb.org/3/configuration",
            params={"api_key": key},
            timeout=10,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=400, detail="Invalid API key — TMDB rejected it. Double-check that you copied the v3 auth key.")
        resp.raise_for_status()
    except _httpx.HTTPError:
        raise HTTPException(status_code=400, detail="Could not reach TMDB to verify the key. Check your internet connection and try again.")
    _set_app_setting(session, "tmdb_api_key", key)
    session.commit()
    return {"ok": True}


@router.get("/profiles")
def get_profiles(session: Session = Depends(get_session)):
    profiles = session.exec(select(UserProfile).order_by(UserProfile.id)).all()
    return [
        {
            "id": p.id,
            "username": p.username,
            "display_name": p.display_name or p.username,
            "has_data": p.has_data,
            "scrape_status": p.scrape_status,
            "scrape_error": p.scrape_error,
            "last_scraped": p.last_scraped.isoformat() if p.last_scraped else None,
        }
        for p in profiles
    ]


@router.post("/profiles")
def save_profile(body: ProfileRequest, session: Session = Depends(get_session)):
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Demo mode: user management is disabled.")
    username = body.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")

    profile = session.exec(
        select(UserProfile).where(UserProfile.username == username)
    ).first()

    if profile:
        if body.display_name.strip():
            profile.display_name = body.display_name.strip()
    else:
        profile = UserProfile(
            username=username,
            display_name=body.display_name.strip() or username,
            scrape_status="pending",
        )
        session.add(profile)

    session.commit()
    session.refresh(profile)

    return {
        "id": profile.id,
        "username": profile.username,
        "display_name": profile.display_name,
        "scrape_status": profile.scrape_status,
    }


@router.post("/profiles/{profile_id}/upload")
async def upload_letterboxd_export(
    profile_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Demo mode: user management is disabled.")
    profile = session.get(UserProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload the .zip file from Letterboxd's data export")

    upload_dir = "/app/data/uploads"
    os.makedirs(upload_dir, exist_ok=True)
    zip_path = os.path.join(upload_dir, f"{profile_id}_{uuid.uuid4().hex}.zip")

    contents = await file.read()
    with open(zip_path, "wb") as fh:
        fh.write(contents)

    profile.scrape_status = "scraping"
    profile.scrape_error = None
    session.add(profile)
    session.commit()

    process_zip_task.delay(profile_id, zip_path)

    return {"id": profile.id, "scrape_status": "scraping"}


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, session: Session = Depends(get_session)):
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Demo mode: user management is disabled.")
    profile = session.get(UserProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    lb_user = session.exec(
        select(LBUser).where(LBUser.username == profile.username)
    ).first()
    if lb_user:
        for r in session.exec(
            select(UserFilmRating).where(UserFilmRating.user_id == lb_user.id)
        ).all():
            session.delete(r)
        session.delete(lb_user)

    session.delete(profile)
    session.commit()
    return {"ok": True}


@router.post("/recommend")
def create_recommendation_job(
    body: RecommendRequest,
    session: Session = Depends(get_session),
):
    profiles = session.exec(select(UserProfile)).all()
    if not profiles:
        raise HTTPException(
            status_code=400, detail="No profiles set up. Visit /setup first."
        )

    still_importing = [
        p.display_name or p.username
        for p in profiles
        if p.scrape_status in ("pending", "scraping")
    ]
    if still_importing:
        raise HTTPException(
            status_code=400,
            detail=f"Still importing: {', '.join(still_importing)}. Check Setup for progress.",
        )

    failed = [p for p in profiles if p.scrape_status == "error"]
    if failed and not any(p.has_data for p in profiles):
        raise HTTPException(
            status_code=400,
            detail="Import failed for all profiles. Visit Setup to retry.",
        )

    job_id = str(uuid.uuid4())
    job = ScrapeJob(
        job_id=job_id,
        username="|".join(p.username for p in profiles),
        genre_ids=json.dumps({
            "include": body.genre_ids,
            "exclude": body.exclude_genre_ids,
            "min_rating": body.min_tmdb_rating,
        }),
        status="pending",
    )
    session.add(job)
    session.commit()

    run_recommendation_job.delay(job_id)
    return {"job_id": job_id}


class VetoRequest(BaseModel):
    vetoed_by: str = ""


@router.post("/veto/{film_id}")
def veto_film(film_id: int, body: VetoRequest = VetoRequest(), session: Session = Depends(get_session)):
    film = session.get(Film, film_id)
    if not film:
        raise HTTPException(status_code=404, detail="Film not found")
    existing = session.exec(select(VetoedFilm).where(VetoedFilm.film_id == film_id)).first()
    if not existing:
        session.add(VetoedFilm(film_id=film_id, vetoed_by=body.vetoed_by or None))
        session.commit()
    return {"ok": True, "film_id": film_id}


@router.delete("/veto/{film_id}")
def un_veto_film(film_id: int, session: Session = Depends(get_session)):
    existing = session.exec(select(VetoedFilm).where(VetoedFilm.film_id == film_id)).first()
    if existing:
        session.delete(existing)
        session.commit()
    return {"ok": True, "film_id": film_id}


@router.get("/vetoes")
def list_vetoes(session: Session = Depends(get_session)):
    rows = session.exec(select(VetoedFilm)).all()
    return [
        {
            "film_id": v.film_id,
            "vetoed_by": v.vetoed_by,
            "vetoed_at": v.vetoed_at.isoformat(),
            "title": session.get(Film, v.film_id).title if session.get(Film, v.film_id) else None,
        }
        for v in rows
    ]


@router.get("/semantic-matching/status")
def semantic_matching_status(session: Session = Depends(get_session)):
    """Check whether sentence-transformers is installed and embeddings are ready."""
    try:
        import sentence_transformers  # noqa: F401
        installed = True
    except ImportError:
        installed = False

    ready_setting = session.exec(
        select(AppSetting).where(AppSetting.key == "semantic_matching_ready")
    ).first()
    embeddings_ready = ready_setting is not None and ready_setting.value == "true"

    computing_setting = session.exec(
        select(AppSetting).where(AppSetting.key == "semantic_matching_computing")
    ).first()
    computing = computing_setting is not None and computing_setting.value == "true"

    from sqlalchemy import func
    # Only count films with overviews — films without can never be embedded
    embeddable = session.exec(
        select(func.count(Film.id)).where(Film.overview != None)  # noqa: E711
    ).one()
    computed = session.exec(
        select(func.count(Film.id)).where(Film.embedding != None)  # noqa: E711
    ).one()

    return {
        "installed": installed,
        "embeddings_ready": embeddings_ready,
        "computing": computing,
        "films_total": embeddable,
        "films_embedded": computed,
    }


@router.post("/semantic-matching/enable")
def enable_semantic_matching(session: Session = Depends(get_session)):
    """Trigger background embedding computation for all films."""
    compute_embeddings_task.delay()
    return {"ok": True, "detail": "Embedding job queued — check /api/semantic-matching/status for progress"}


class EmbeddingConfigRequest(BaseModel):
    provider: str = "local"         # "local" or "remote"
    remote_url: str = ""            # e.g. http://192.168.1.x:1234/v1
    remote_model: str = ""          # model name as shown in LM Studio / Ollama
    remote_key: str = "lm-studio"  # API key (ignored by local LM Studio, needed for OpenAI)


@router.get("/embedding-config")
def get_embedding_config(session: Session = Depends(get_session)):
    return {
        "provider": _get_app_setting(session, "embedding_provider", "local"),
        "remote_url": _get_app_setting(session, "embedding_remote_url", ""),
        "remote_model": _get_app_setting(session, "embedding_remote_model", ""),
        "remote_key_set": bool(_get_app_setting(session, "embedding_remote_key", "")),
    }


@router.post("/embedding-config")
def save_embedding_config(body: EmbeddingConfigRequest, session: Session = Depends(get_session)):
    _set_app_setting(session, "embedding_provider", body.provider)
    _set_app_setting(session, "embedding_remote_url", body.remote_url.strip())
    _set_app_setting(session, "embedding_remote_model", body.remote_model.strip())
    if body.remote_key:
        _set_app_setting(session, "embedding_remote_key", body.remote_key.strip())
    session.commit()
    return {"ok": True}


@router.post("/embedding-config/test")
def test_embedding_config(body: EmbeddingConfigRequest):
    """Test a remote embedding provider by embedding a short string."""
    if body.provider == "local":
        try:
            from sentence_transformers import SentenceTransformer
            SentenceTransformer("all-MiniLM-L6-v2").encode(["test"])
            return {"ok": True, "detail": "Local model loaded successfully"}
        except ImportError:
            return {"ok": False, "detail": "sentence-transformers is not installed in the container"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    from app.tasks.scrape_user import _remote_embeddings
    try:
        vecs = _remote_embeddings(
            body.remote_url.strip(),
            body.remote_model.strip(),
            body.remote_key or "lm-studio",
            ["Letterboxd recommender test"],
        )
        dim = len(vecs[0]) if vecs else 0
        return {"ok": True, "detail": f"Connected — embedding dimension: {dim}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@router.post("/embedding-config/clear")
def clear_embeddings(session: Session = Depends(get_session)):
    """Clear all stored embeddings and reset the ready flag (required when switching models)."""
    films = session.exec(select(Film).where(Film.embedding != None)).all()  # noqa: E711
    for f in films:
        f.embedding = None
        session.add(f)
    _set_app_setting(session, "semantic_matching_ready", "false")
    _set_app_setting(session, "semantic_matching_computing", "false")
    session.commit()
    return {"ok": True, "cleared": len(films)}


@router.post("/refresh")
def trigger_refresh():
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Demo mode: user management is disabled.")
    refresh_all_profiles.delay()
    return {"ok": True}


@router.get("/status/{job_id}")
def get_job_status(job_id: str, session: Session = Depends(get_session)):
    job = session.exec(select(ScrapeJob).where(ScrapeJob.job_id == job_id)).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    resp = {
        "job_id": job_id,
        "status": job.status,
        "usernames": job.username,
        "error": job.error_message,
    }
    if job.status == "complete" and job.result_json:
        resp["results"] = json.loads(job.result_json)
    return resp
