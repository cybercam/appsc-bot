"""Application configuration loaded from environment / ``.env``.

Uses :mod:`pydantic_settings` so every value is validated and typed at
startup. Import :data:`settings` anywhere you need configuration.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Directory containing this file (project root for relative paths).
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Strongly-typed application settings.

    All fields are read from environment variables (case-insensitive) or a
    local ``.env`` file. See ``.env.example`` for documentation of each one.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = Field(..., description="BotFather token")

    google_service_account_file: str = Field(
        default="service_account.json",
        description="Path to the service-account JSON credentials.",
    )
    sheet_id: str = Field(..., description="Google Sheet ID (from the URL).")
    worksheet_name: str = Field(default="Sheet1")

    timezone: str = Field(default="Asia/Kolkata")
    daily_review_hour: int = Field(default=7, ge=0, le=23)

    # Comma-separated chat IDs that may use the bot. Empty => allow everyone
    # that has run /start (recorded in chat_ids.json).
    authorized_chat_ids: str = Field(default="")

    # Where /start persists registered chat IDs.
    chat_ids_file: str = Field(default="chat_ids.json")

    log_level: str = Field(default="INFO")
    log_file: str = Field(default="logs/bot.log")

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @property
    def authorized_ids(self) -> set[int]:
        """Parse :attr:`authorized_chat_ids` into a set of ints."""
        ids: set[int] = set()
        for chunk in self.authorized_chat_ids.split(","):
            chunk = chunk.strip()
            if chunk:
                try:
                    ids.add(int(chunk))
                except ValueError:
                    continue
        return ids

    @property
    def service_account_path(self) -> Path:
        """Absolute path to the service-account credentials file."""
        p = Path(self.google_service_account_file)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def chat_ids_path(self) -> Path:
        p = Path(self.chat_ids_file)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def log_path(self) -> Path:
        p = Path(self.log_file)
        return p if p.is_absolute() else BASE_DIR / p

    def is_authorized(self, chat_id: int, registered: set[int]) -> bool:
        """Return whether ``chat_id`` may interact with the bot.

        If :attr:`authorized_chat_ids` is configured, only those IDs are
        allowed. Otherwise any chat that has previously run ``/start`` (and
        thus appears in ``registered``) is allowed.
        """
        if self.authorized_ids:
            return chat_id in self.authorized_ids
        return chat_id in registered


# Singleton accessor ------------------------------------------------------- #
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the lazily-instantiated :class:`Settings` singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
