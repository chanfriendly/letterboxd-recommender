from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.models.db import create_db_and_tables
from app.models.film import VetoedFilm  # noqa: F401 — ensures table is registered
from app.routers import api, ui

app = FastAPI(title="Letterboxd Recommender", version="0.1.0")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(ui.router)
app.include_router(api.router)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()
