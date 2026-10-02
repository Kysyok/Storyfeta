"""Application configuration, read only from environment variables (12-factor).

See ``.env.example`` for every variable. Nothing secret has a default.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.game.engine import Rules
from app.models.domain import Settings as GameSettings


class Settings(BaseSettings):
    """Typed settings. ``.env`` is read for local development only."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Web process
    port: int = 8000
    app_env: str = "development"
    log_level: str = "INFO"
    shutdown_timeout_seconds: int = 10

    # Backing services
    redis_url: str = "redis://localhost:6379/0"
    database_url: str | None = Field(
        default=None,
        description="postgresql+asyncpg://... ; leave empty to disable host accounts",
    )

    # Sessions
    secret_key: SecretStr
    cookie_secure: bool = False
    player_token_ttl_seconds: int = 60 * 60 * 48
    account_token_ttl_seconds: int = 60 * 60 * 24 * 30

    # Rooms and game rules
    room_code_length: int = 4
    room_ttl_seconds: int = 60 * 60 * 24
    room_idle_seconds: int = 60 * 60 * 6
    cleanup_interval_seconds: int = 300
    timer_poll_seconds: float = 0.5
    default_rounds: int = 5
    default_turn_seconds: int = 60
    max_players: int = 12
    max_line_length: int = 280
    allowed_reactions: str = "🔥"

    @property
    def accounts_enabled(self) -> bool:
        """Host accounts need PostgreSQL; without it the app is guest-only."""
        return bool(self.database_url)

    @property
    def rules(self) -> Rules:
        """Engine limits derived from the environment."""
        reactions = tuple(e.strip() for e in self.allowed_reactions.split(",") if e.strip())
        return Rules(
            max_players=self.max_players,
            max_line_length=self.max_line_length,
            allowed_reactions=reactions or ("🔥",),
        )

    @property
    def default_game_settings(self) -> GameSettings:
        """Settings a brand-new room starts with."""
        return GameSettings(
            rounds=self.default_rounds, turn_seconds=self.default_turn_seconds
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]  # secret_key comes from the environment
