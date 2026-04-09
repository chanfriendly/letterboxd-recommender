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

    embedding: Optional[str] = None  # JSON float list from sentence-transformers, null until computed

    genres: List[Genre] = Relationship(back_populates="films", link_model=FilmGenreLink)
    ratings: List["UserFilmRating"] = Relationship(back_populates="film")


class FilmKeyword(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tmdb_keyword_id: int = Field(unique=True, index=True)
    name: str


class FilmKeywordLink(SQLModel, table=True):
    film_id: Optional[int] = Field(default=None, foreign_key="film.id", primary_key=True)
    keyword_id: Optional[int] = Field(default=None, foreign_key="filmkeyword.id", primary_key=True)


class FilmPerson(SQLModel, table=True):
    """A director or cast member from TMDB credits."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tmdb_person_id: int = Field(unique=True, index=True)
    name: str


class FilmPersonLink(SQLModel, table=True):
    """Links a film to a person with a role tag ('director' or 'cast')."""
    film_id: Optional[int] = Field(default=None, foreign_key="film.id", primary_key=True)
    person_id: Optional[int] = Field(default=None, foreign_key="filmperson.id", primary_key=True)
    role: str = Field(primary_key=True)  # "director" or "cast"


class AppSetting(SQLModel, table=True):
    """Key/value store for user-configurable feature flags."""
    key: str = Field(primary_key=True)
    value: str  # stored as string; cast at read time


class VetoedFilm(SQLModel, table=True):
    """Films the group has permanently excluded from recommendations."""
    id: Optional[int] = Field(default=None, primary_key=True)
    film_id: int = Field(foreign_key="film.id", index=True, unique=True)
    vetoed_by: Optional[str] = None  # username of whoever hit veto, for display
    vetoed_at: datetime = Field(default_factory=datetime.utcnow)
