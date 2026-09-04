"""
app/config.py

Single source of truth for runtime configuration. Everything is read from
environment variables (see .env.example) — nothing here is a secret, and
nothing in the repo hardcodes a secret. `Settings` is instantiated once
and imported everywhere else that needs config.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = "development"
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = "sqlite:///./control_plane.db"

    # --- Auth ---
    jwt_secret: str = "change-me-in-production-this-is-not-secret"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    demo_auth_enabled: bool = True
    demo_auth_password: str = "demo-password"

    # --- Payments ---
    demo_mode: bool = True
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_base_url: str = "https://api.razorpay.com/v1"

    # --- Agent / LLM ---
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    agent_force_fallback: bool = False

    # --- CORS ---
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:8000"

    # --- Rate limiting ---
    rate_limit_per_minute: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
