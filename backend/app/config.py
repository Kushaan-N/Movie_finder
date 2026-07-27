"""Application configuration, loaded from environment / .env.

Keeping this centralized (and typed) means swapping SQLite for Postgres or
turning providers on/off is a config change, not a code change.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (backend/app/config.py -> repo/).
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Providers
    serpapi_key: str = ""
    movieglu_api_key: str = ""
    movieglu_client: str = ""
    movieglu_authorization: str = ""
    movieglu_territory: str = "US"
    movieglu_api_version: str = "v201"
    enable_scraper_fallback: bool = False
    google_places_api_key: str = ""

    # Database
    database_url: str = "sqlite:///./showtime_finder.db"

    # App
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    scrape_rate_limit_per_sec: float = 0.5
    # Cache identical searches briefly to conserve the SerpApi free-tier quota
    # (100/month) and speed up repeat/form-tweak searches. 0 disables the cache.
    search_cache_ttl_sec: float = 300

    # Config file locations (editable JSON at repo root).
    theaters_file: str = str(REPO_ROOT / "theaters.json")
    row_mappings_file: str = str(REPO_ROOT / "row_mappings.json")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_serpapi(self) -> bool:
        return bool(self.serpapi_key)

    @property
    def has_movieglu(self) -> bool:
        return bool(self.movieglu_api_key and self.movieglu_authorization)


@lru_cache
def get_settings() -> Settings:
    return Settings()
