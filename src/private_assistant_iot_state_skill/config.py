"""Configuration models for the IoT State Skill.

This module defines the configuration structure required for the IoT State Skill
to connect to TimescaleDB and integrate with the private assistant ecosystem.
"""

import private_assistant_commons as commons


class SkillConfig(commons.SkillConfig):
    """Configuration model for IoT State Skill.

    Extends the base SkillConfig from private-assistant-commons with
    IoT-specific database connection parameters.

    Attributes:
        iot_postgres_user: PostgreSQL username for IoT database connection
        iot_postgres_db: Name of the PostgreSQL database containing IoT data
        iot_postgres_host: Hostname or IP address of the PostgreSQL server
        iot_postgres_port: Port number for PostgreSQL connection (default: 5432)
    """

    # AIDEV-NOTE: Database connection settings for TimescaleDB IoT data storage
    iot_postgres_user: str = "postgres"
    iot_postgres_db: str = "postgres"
    iot_postgres_host: str = "localhost"
    iot_postgres_port: int = 5432
