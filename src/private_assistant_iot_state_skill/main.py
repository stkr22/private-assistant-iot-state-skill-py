import asyncio
import pathlib
from typing import Annotated

import jinja2
import typer
from private_assistant_commons import mqtt_connection_handler, skill_config, skill_logger
from sqlalchemy.ext.asyncio import create_async_engine

from private_assistant_iot_state_skill import config, iot_state_skill

app = typer.Typer()


@app.command()
def main(
    config_path: Annotated[pathlib.Path, typer.Argument(envvar="PRIVATE_ASSISTANT_CONFIG_PATH")],
    iot_postgres_password: Annotated[
        str,
        typer.Option(
            envvar="IOT_POSTGRES_PASSWORD",
            help="IoT PostgreSQL password",
            prompt=True,
            hide_input=True,
        ),
    ] = "postgres",
) -> None:
    asyncio.run(start_skill(config_path, iot_postgres_password))


async def start_skill(config_path: pathlib.Path, iot_postgres_password: str):
    logger = skill_logger.SkillLogger.get_logger("Private Assistant IoTStateSkill")
    # Load the configuration
    config_obj = skill_config.load_config(config_path, config.SkillConfig)
    db_engine_async = create_async_engine(
        url=f"postgresql+asyncpg://{config_obj.iot_postgres_user}:{iot_postgres_password}@{config_obj.iot_postgres_host}:{config_obj.iot_postgres_port}/{config_obj.iot_postgres_db}"
    )
    template_env = jinja2.Environment(
        loader=jinja2.PackageLoader(
            "private_assistant_iot_state_skill",
            "templates",
        )
    )
    await mqtt_connection_handler.mqtt_connection_handler(
        iot_state_skill.IoTStateSkill,
        config_obj,
        5,
        logger=logger,
        template_env=template_env,
        db_engine=db_engine_async,
    )


if __name__ == "__main__":
    app()
