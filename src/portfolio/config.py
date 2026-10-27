from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PORTFOLIO_", extra="ignore")

    database_url: str = "sqlite:///./data/portfolio.db"
    max_upload_mb: int = 20
    # Statements older than this are flagged on the dashboard.
    stale_after_days: int = 45
    # Allow downloading AMFI's public NAV file when you click Refresh. The only outbound request
    # the app makes; it sends nothing about your holdings.
    fetch_prices: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
