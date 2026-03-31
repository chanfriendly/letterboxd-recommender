from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class ScrapeJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(unique=True, index=True)
    username: str
    genre_ids: str = ""  # comma-separated genre IDs
    status: str = "pending"  # pending | running | complete | failed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    result_json: Optional[str] = None  # JSON-serialized recommendation list
