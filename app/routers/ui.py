from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import settings
from app.models.db import get_session
from app.models.profile import UserProfile
from app.models.user import LBUser
from app.recommender.affinity import build_genre_affinity, build_keyword_affinity
from app.models.film import Genre, FilmKeyword

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request, "genres": TMDB_GENRES, "demo_mode": settings.demo_mode}
    )


@router.get("/setup", response_class=HTMLResponse)
async def setup(request: Request):
    if settings.demo_mode:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("setup.html", {"request": request, "demo_mode": False})


@router.get("/methodology", response_class=HTMLResponse)
async def methodology(request: Request, session: Session = Depends(get_session)):
    profiles = session.exec(select(UserProfile).order_by(UserProfile.id)).all()

    # Build live genre affinity data per user
    user_affinities = []
    for profile in profiles:
        user = session.exec(select(LBUser).where(LBUser.username == profile.username)).first()
        if not user:
            continue
        genre_aff = build_genre_affinity(session, user.id)
        kw_aff = build_keyword_affinity(session, user.id)
        if not genre_aff and not kw_aff:
            continue
        genre_rows = []
        for genre_id, avg_rating in sorted(genre_aff.items(), key=lambda x: x[1], reverse=True):
            genre = session.get(Genre, genre_id)
            if genre:
                genre_rows.append({"name": genre.name, "avg_rating": round(avg_rating, 2)})
        keyword_rows = []
        for kid, avg_rating in sorted(kw_aff.items(), key=lambda x: x[1], reverse=True)[:15]:
            kw = session.get(FilmKeyword, kid)
            if kw:
                keyword_rows.append({"name": kw.name, "avg_rating": round(avg_rating, 2)})
        user_affinities.append({
            "display_name": profile.display_name or profile.username,
            "genres": genre_rows[:10],
            "keywords": keyword_rows,
        })

    return templates.TemplateResponse(
        "methodology.html",
        {
            "request": request,
            "user_affinities": user_affinities,
            "cf_threshold": settings.cf_cold_start_threshold,
            "demo_mode": settings.demo_mode,
        },
    )
