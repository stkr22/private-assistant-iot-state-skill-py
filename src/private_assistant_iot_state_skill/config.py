import private_assistant_commons as commons


class SkillConfig(commons.SkillConfig):
    iot_postgres_user: str = "postgres"
    iot_postgres_db: str = "postgres"
    iot_postgres_host: str = "localhost"
    iot_postgres_port: int = 5432
