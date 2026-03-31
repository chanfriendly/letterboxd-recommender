import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.models.db import get_session
from app.models.job import ScrapeJob
from app.tasks.scrape_user import run_recommendation_job

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
    username: str
    genre_ids: list[int] = []


@router.get("/genres")
def list_genres():
    return TMDB_GENRES


@router.post("/recommend")
def create_recommendation_job(
    body: RecommendRequest,
    session: Session = Depends(get_session),
):
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Username is required")

    job_id = str(uuid.uuid4())
    job = ScrapeJob(
        job_id=job_id,
        username=body.username.strip().lower(),
        genre_ids=",".join(str(g) for g in body.genre_ids),
        status="pending",
    )
    session.add(job)
    session.commit()

    run_recommendation_job.delay(job_id)

    return {"job_id": job_id}


@router.get("/status/{job_id}")
def get_job_status(job_id: str, session: Session = Depends(get_session)):
    job = session.exec(select(ScrapeJob).where(ScrapeJob.job_id == job_id)).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    response = {
        "job_id": job_id,
        "status": job.status,
        "username": job.username,
        "error": job.error_message,
    }

    if job.status == "complete" and job.result_json:
        response["results"] = json.loads(job.result_json)

    return response
