from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class UserProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    display_name: str = ""
    password: str = ""
    has_data: bool = False
    scrape_status: str = "pending"   # pending | scraping | ready | error
    scrape_error: Optional[str] = None
    last_scraped: Optional[datetime] = None
