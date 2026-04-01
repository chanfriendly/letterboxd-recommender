from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship


class FilmGenreLink(SQLModel, table=True):
    film_id: Optional[int] = Field(default=None, foreign_key="film.id", primary_key=True)
    genre_id: Optional[int] = Field(default=None, foreign_key="genre.id", primary_key=True)


class Genre(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tmdb_genre_id: int = Field(unique=True, index=True)
    name: str

    films: List["Film"] = Relationship(back_populates="genres", link_model=FilmGenreLink)


class Film(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    letterboxd_slug: str = Field(unique=True, index=True)
    tmdb_id: Optional[int] = Field(default=None, index=True)
    title: str
    year: Optional[int] = None
    poster_url: Optional[str] = None
    overview: Optional[str] = None
    tmdb_rating: Optional[float] = None
    lb_rating: Optional[float] = None

    genres: List[Genre] = Relationship(back_populates="films", link_model=FilmGenreLink)
    ratings: List["UserFilmRating"] = Relationship(back_populates="film")


class VetoedFilm(SQLModel, table=True):
    """Films the group has permanently excluded from recommendations."""
    id: Optional[int] = Field(default=None, primary_key=True)
    film_id: int = Field(foreign_key="film.id", index=True, unique=True)
    vetoed_by: Optional[str] = None  # username of whoever hit veto, for display
    vetoed_at: datetime = Field(default_factory=datetime.utcnow)
