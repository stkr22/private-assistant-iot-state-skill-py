"""CLI entry point for the Private Assistant IoT State Skill.

This module provides the command-line interface and startup logic for the
IoT State Skill, handling configuration loading, database connections,
and MQTT integration.
"""

import asyncio
import pathlib
from typing import Annotated

import jinja2
import typer
from private_assistant_commons import SkillConfig, mqtt_connection_handler, skill_config, skill_logger
from private_assistant_commons.database import PostgresConfig
from sqlalchemy.ext.asyncio import create_async_engine

from private_assistant_iot_state_skill import config, iot_state_skill

app = typer.Typer()


@app.command()
def main(
    config_path: Annotated[pathlib.Path, typer.Argument(envvar="PRIVATE_ASSISTANT_CONFIG_PATH")],
) -> None:
    """Start the Private Assistant IoT State Skill.

    Database passwords are loaded from environment variables:
    - POSTGRES_PASSWORD: Assistant database (device registry)
    - IOT_POSTGRES_PASSWORD: TimescaleDB (IoT data storage)

    Args:
        config_path: Path to the skill configuration file (TOML format)
    """
    asyncio.run(start_skill(config_path))


async def start_skill(config_path: pathlib.Path) -> None:
    """Initialize and start the IoT State Skill.

    Sets up logging, loads configuration, creates database connections,
    initializes Jinja2 template environment, and starts the MQTT connection
    handler for the skill.

    Args:
        config_path: Path to the configuration file
    """
    logger = skill_logger.SkillLogger.get_logger("Private Assistant IoTStateSkill")

    # AIDEV-NOTE: Configuration loading from TOML file with environment variable support
    config_obj = skill_config.load_config(config_path, SkillConfig)

    # AIDEV-NOTE: Assistant database for global device registry (POSTGRES_* env vars)
    assistant_db_engine = create_async_engine(PostgresConfig().connection_string_async)

    # AIDEV-NOTE: TimescaleDB for IoT data storage (IOT_POSTGRES_* env vars)
    iot_db_engine = create_async_engine(config.TimescalePostgresConfig().connection_string_async)

    # AIDEV-NOTE: Jinja2 template environment for response generation
    template_env = jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_iot_state_skill",
            "templates",
        )
    )

    # AIDEV-NOTE: Start MQTT connection handler with 5-second reconnection interval
    await mqtt_connection_handler.mqtt_connection_handler(
        iot_state_skill.IoTStateSkill,
        config_obj,
        5,
        logger=logger,
        template_env=template_env,
        assistant_engine=assistant_db_engine,
        iot_db_engine=iot_db_engine,
    )


if __name__ == "__main__":
    app()
