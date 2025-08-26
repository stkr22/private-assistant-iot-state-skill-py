"""Private Assistant IoT State Skill package.

A distributed voice assistant skill for querying IoT device states from TimescaleDB.
Integrates with the private-assistant-commons framework to handle natural language
queries about home automation devices through MQTT communication.

Main Components:
    - IoTStateSkill: Core skill implementation for device state queries
    - SkillConfig: Configuration model for database and MQTT settings
    - main: CLI entry point and application startup logic

Example Usage:
    Run the skill from command line:
    ```
    uv run private-assistant-iot-state-skill /path/to/config.toml
    ```

    Or import programmatically:
    ```python
    from private_assistant_iot_state_skill import IoTStateSkill, SkillConfig
    ```
"""
