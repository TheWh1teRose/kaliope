"""Application settings.

Required values are validated at import of :func:`get_settings`, so a missing
key fails the process at boot with a readable message rather than at the first
request that happens to need it (AC-DEP-3).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised at startup when the environment is not usable."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_secret_key: str = ""
    data_dir: Path = Path("/data")

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    default_model: str = "claude-opus-4-6"
    zone_model: str = ""

    max_concurrent_runs: int = 2
    log_level: str = "INFO"
    session_ttl_days: int = 30
    max_upload_mb: int = 50

    # Set by the test suite and by `cli.py` when it needs a throwaway database.
    testing: bool = False

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # ---- derived paths -------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kalliope.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.artifacts_dir, self.uploads_dir, self.renders_dir):
            path.mkdir(parents=True, exist_ok=True)

    def validate_required(self) -> None:
        """Fail fast on an unusable environment."""
        missing: list[str] = []
        if not self.app_secret_key.strip():
            missing.append("APP_SECRET_KEY")
        if missing:
            raise ConfigurationError(
                "Missing required configuration: "
                + ", ".join(missing)
                + ". Set them in the environment or in .env — see .env.example."
            )
        if len(self.app_secret_key) < 16:
            raise ConfigurationError(
                "APP_SECRET_KEY is too short; use at least 16 characters. "
                'Generate one with: python -c "import secrets; '
                'print(secrets.token_urlsafe(48))"'
            )

    def has_any_llm_key(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key or self.google_api_key)


_override: Settings | None = None


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    """Return the process-wide settings object."""
    if _override is not None:
        return _override
    return _cached_settings()


def set_settings(settings: Settings | None) -> None:
    """Install a settings override. Used by tests and by the CLI."""
    global _override
    _override = settings


DEFAULT_SETTINGS_FIELDS: tuple[str, ...] = tuple(Settings.model_fields.keys())
