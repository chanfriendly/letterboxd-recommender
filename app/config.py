from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    tmdb_api_key: str = ""
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite:///./letterboxd_rec.db"
    lb_rate_limit_rps: float = 1.0
    cf_max_audience_pages: int = 5
    cf_cold_start_threshold: int = 20
    cache_ttl_seconds: int = 21600
    scrape_cache_hours: int = 24
    demo_mode: bool = False


settings = Settings()
