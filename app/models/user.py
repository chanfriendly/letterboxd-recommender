from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship
from app.models.film import Film


class LBUser(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    scraped_at: Optional[datetime] = None
    film_count: int = 0
    is_audience_user: bool = False  # True = scraped from film pages, not primary user

    ratings: List["UserFilmRating"] = Relationship(back_populates="user")


class UserFilmRating(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="lbuser.id", index=True)
    film_id: int = Field(foreign_key="film.id", index=True)
    rating: Optional[float] = None  # 0.5–5.0, None = watched but unrated
    watched_at: Optional[datetime] = None

    user: Optional[LBUser] = Relationship(back_populates="ratings")
    film: Optional[Film] = Relationship(back_populates="ratings")
