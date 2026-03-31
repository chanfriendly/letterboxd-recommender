from celery import Celery
from app.config import settings

celery_app = Celery(
    "letterboxd_recommender",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.scrape_user"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # one task at a time per worker (scraping is slow)
)
