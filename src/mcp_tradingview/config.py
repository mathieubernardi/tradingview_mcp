"""Configuration settings for MCP TradingView server."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TransportMode(StrEnum):
    STDIO = "stdio"
    SSE = "sse"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TV_",
        case_sensitive=False,
        extra="ignore",
    )

    # TradingView credentials (optional — anonymous has rate limits)
    tv_username: str = Field(default="", description="TradingView username")
    tv_password: str = Field(default="", description="TradingView password")

    # Server config
    transport: TransportMode = Field(
        default=TransportMode.STDIO,
        description="Transport mode: stdio | sse",
    )
    host: str = Field(default="127.0.0.1", description="SSE server host")
    port: int = Field(default=8765, description="SSE server port")

    # Defaults for data fetching
    default_exchange: str = Field(
        default="EURONEXT", description="Default exchange (PEA: EURONEXT)"
    )
    default_n_bars: int = Field(default=500, ge=10, le=5000)
    max_n_bars: int = Field(default=5000, ge=100, le=10000)

    # Cache
    cache_ttl_seconds: int = Field(default=60, ge=0)

    log_level: LogLevel = Field(default=LogLevel.INFO)

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1024 <= v <= 65535):
            raise ValueError("Port must be between 1024 and 65535")
        return v


# Singleton
settings = Settings()
