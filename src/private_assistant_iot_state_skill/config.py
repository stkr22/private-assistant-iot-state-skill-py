"""Configuration models for the IoT State Skill.

This module defines the configuration structure required for the IoT State Skill
to connect to TimescaleDB and integrate with the private assistant ecosystem.

Note: This skill uses the base SkillConfig from private-assistant-commons directly
without customization. Import it from commons.SkillConfig when needed.
"""

from private_assistant_commons.database import PostgresConfig
from pydantic import AliasChoices, Field
from pydantic_settings import SettingsConfigDict


class TimescalePostgresConfig(PostgresConfig):
    """TimescaleDB connection configuration.

    Inherits from PostgresConfig but uses IOT_POSTGRES_ environment variable prefix
    to separate TimescaleDB connection from the Assistant database connection.

    Environment variables:
        IOT_POSTGRES_USER: Database user (default: postgres)
        IOT_POSTGRES_PASSWORD: Database password (default: postgres)
        IOT_POSTGRES_HOST: Database host (default: localhost)
        IOT_POSTGRES_PORT: Database port (default: 5432)
        IOT_POSTGRES_DB: Database name (default: postgres)

    Example:
        >>> # Automatically loads from IOT_POSTGRES_* environment variables
        >>> from sqlalchemy.ext.asyncio import create_async_engine
        >>> config = TimescalePostgresConfig()
        >>> engine = create_async_engine(
        ...     config.connection_string_async,
        ...     pool_pre_ping=True,
        ...     pool_recycle=3600,
        ...     connect_args={"command_timeout": 60}
        ... )
    """

    model_config = SettingsConfigDict(env_prefix="IOT_POSTGRES_")

    # AIDEV-NOTE: Override database field to use IOT_POSTGRES_DB env var
    database: str = Field(
        default="postgres",
        description="Database name",
        validation_alias=AliasChoices("database", "IOT_POSTGRES_DB"),
    )
