"""Core implementation of the Private Assistant IoT State Skill.

This module contains the main skill logic for querying IoT device states
from TimescaleDB, processing natural language intents, and generating
responses through the private assistant framework.
"""

from datetime import datetime, timedelta
from enum import Enum

import jinja2
import mqtt_ingest_pipeline.iot_data_transformer as models
from private_assistant_commons import messages, skill_config
from private_assistant_commons.base_skill import BaseSkill
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession


class DeviceType(Enum):
    """Supported IoT device types.

    Maps device categories to their database identifiers for querying
    IoT data from the TimescaleDB.
    """

    WINDOW = "window_sensor"


class Action(Enum):
    """Available skill actions.

    Defines the types of queries this skill can handle. Currently
    only supports state queries but designed for future extensibility.
    """

    STATE_QUERY = "state_query"


class StateFilter(Enum):
    """Device state filters for query refinement.

    Allows users to filter device states based on their current condition.
    Used for natural language processing of queries like 'open windows'.
    """

    ALL = "all"
    OPEN = "open"
    CLOSED = "closed"


class Parameters(BaseModel):
    """Request parameters extracted from natural language intent.

    Contains all the information needed to process a user's query about
    IoT device states, including the target devices, rooms, and filters.

    Attributes:
        action: The type of action to perform (currently only STATE_QUERY)
        device_type: The category of device being queried (e.g., WINDOW)
        rooms: List of room names to filter the query
        state_filter: Filter for device states (all/open/closed)
        states: Query results as list of (device_name, room) tuples
    """

    action: Action = Action.STATE_QUERY
    device_type: DeviceType
    rooms: list[str] = []
    state_filter: StateFilter = StateFilter.ALL
    states: list[tuple] = []


class IoTStateSkill(BaseSkill):
    """Private Assistant skill for querying IoT device states.

    Integrates with the private-assistant-commons framework to handle
    voice queries about IoT device states stored in TimescaleDB. Uses
    confidence-based competition with other skills to determine which
    queries to handle.

    The skill supports natural language queries like:
    - "Are there any open windows in the living room?"
    - "Show me all window states"
    - "Which windows are closed?"

    Attributes:
        db_engine: Async SQLAlchemy engine for TimescaleDB connections
        template_env: Jinja2 environment for response generation
        action_to_template: Mapping of actions to their Jinja2 templates
        device_type_map: Mapping of keywords to supported device types
    """

    def __init__(
        self,
        config_obj: skill_config.SkillConfig,
        template_env: jinja2.Environment,
        db_engine: AsyncEngine,
        **kwargs,
    ) -> None:
        """Initialize the IoT State Skill.

        Args:
            config_obj: Skill configuration with database settings
            template_env: Jinja2 environment for template rendering
            db_engine: Async database engine for IoT data queries
            **kwargs: Additional arguments passed to BaseSkill
        """
        super().__init__(config_obj=config_obj, **kwargs)
        self.db_engine = db_engine
        self.template_env = template_env
        self.action_to_template: dict[Action, jinja2.Template] = {}

        # AIDEV-NOTE: Keyword mapping for natural language device type detection
        self.device_type_map: dict[str, DeviceType] = {
            "window": DeviceType.WINDOW,
            "windows": DeviceType.WINDOW,
        }

    def _load_templates(self) -> None:
        """Load Jinja2 templates for response generation.

        Loads all templates corresponding to available actions from the
        templates directory. Templates follow the naming convention:
        {action_name}.j2 (e.g., state_query.j2)

        Raises:
            jinja2.TemplateNotFound: If any required template is missing
        """
        try:
            # AIDEV-NOTE: Dynamic template loading based on available actions
            for action in Action:
                self.action_to_template[action] = self.template_env.get_template(f"{action.name.lower()}.j2")
            self.logger.debug("Templates loaded successfully")
        except jinja2.TemplateNotFound as e:
            self.logger.error("Failed to load template: %s", e)

    async def skill_preparations(self) -> None:
        """Perform skill initialization tasks.

        Called once during skill startup to load templates and prepare
        the skill for handling requests. Part of the BaseSkill lifecycle.
        """
        self._load_templates()

    async def calculate_certainty(self, intent_analysis_result: messages.IntentAnalysisResult) -> float:
        """Calculate confidence score for handling the given intent.

        Analyzes the intent's nouns to determine if this skill can handle
        the request. Returns maximum confidence (1.0) if any supported
        device type keywords are found, otherwise returns 0.0.

        Args:
            intent_analysis_result: Processed natural language intent with extracted entities

        Returns:
            float: Confidence score between 0.0 and 1.0
        """
        # AIDEV-NOTE: Simple keyword-based confidence calculation - could be enhanced with ML
        return 1.0 if any(noun in self.device_type_map for noun in intent_analysis_result.nouns) else 0.0

    def get_parameters(self, intent_analysis_result: messages.IntentAnalysisResult) -> Parameters:
        """Extract query parameters from natural language intent.

        Analyzes the intent to determine:
        - Which device type is being queried
        - Which rooms to search (from intent or request origin)
        - What state filter to apply (open/closed/all)

        Args:
            intent_analysis_result: Processed natural language intent

        Returns:
            Parameters: Structured parameters for database query

        Raises:
            ValueError: If no supported device type is found in the request
        """
        # AIDEV-NOTE: Device type detection using keyword mapping
        device_type = next(
            (self.device_type_map[noun] for noun in intent_analysis_result.nouns if noun in self.device_type_map), None
        )
        if not device_type:
            raise ValueError("No valid device type found in request")

        # AIDEV-NOTE: Room resolution - use specified rooms or fallback to request origin
        rooms = intent_analysis_result.rooms or [intent_analysis_result.client_request.room]

        # AIDEV-NOTE: State filter extraction from natural language text
        text_words = set(intent_analysis_result.client_request.text.lower().split())
        state_filter = (
            StateFilter.OPEN
            if "open" in text_words
            else StateFilter.CLOSED
            if "closed" in text_words
            else StateFilter.ALL
        )

        return Parameters(device_type=device_type, rooms=rooms, state_filter=state_filter)

    async def get_device_states(self, params: Parameters) -> list[tuple]:
        """Query TimescaleDB for current device states.

        Retrieves the most recent state for each device matching the query
        parameters. Uses a window function to get the latest reading per device
        and applies state filtering if requested.

        Args:
            params: Query parameters with device type, rooms, and state filter

        Returns:
            list[tuple]: List of (device_name, room) tuples matching the query
        """
        # AIDEV-NOTE: Room name normalization - database stores rooms without spaces
        rooms_wo_whitespace = [room.replace(" ", "") for room in params.rooms]

        async with AsyncSession(self.db_engine) as session:
            # AIDEV-QUESTION: Consider indexing strategy for device_id, time, and room columns
            subquery = (
                select(
                    models.IoTData,
                    func.row_number()
                    .over(partition_by=models.IoTData.device_id, order_by=col(models.IoTData.time).desc())
                    .label("row_num"),
                )
                .where(models.IoTData.device_type == params.device_type.value)
                .where(models.IoTData.time > datetime.now() - timedelta(days=2))  # AIDEV-NOTE: 2-day window
                .where(col(models.IoTData.room).in_(rooms_wo_whitespace))
            ).subquery()

            # AIDEV-NOTE: State filtering based on contact sensor payload
            if params.state_filter != StateFilter.ALL:
                is_closed = params.state_filter != StateFilter.OPEN
                filter_query = select(subquery.c.device_name, subquery.c.room).where(
                    subquery.c.payload["contact"].astext == str(is_closed).lower()
                )
            query = filter_query.where(subquery.c.row_num == 1)
            result = await session.exec(query)
            return list(result.all())

    def get_answer(self, params: Parameters) -> str:
        """Generate natural language response using Jinja2 templates.

        Renders the appropriate template with query results to create
        a human-readable response for the voice assistant.

        Args:
            params: Query parameters including results from database

        Returns:
            str: Natural language response for the user
        """
        template = self.action_to_template.get(params.action)
        if template:
            return template.render(params=params)

        # AIDEV-NOTE: Graceful degradation when template is missing
        self.logger.error("No template found for action %s", params.action)
        return "Sorry, I couldn't process your request"

    async def process_request(self, intent: messages.IntentAnalysisResult) -> None:
        """Process a user's natural language request about IoT devices.

        Main entry point for handling voice queries. Extracts parameters,
        queries the database, generates a response, and sends it back
        through the MQTT broker.

        Args:
            intent: Processed natural language intent with extracted entities
        """
        # AIDEV-NOTE: Request processing pipeline: params -> query -> response
        params = self.get_parameters(intent)
        params.states = await self.get_device_states(params)
        response = self.get_answer(params)
        await self.send_response(response, intent.client_request)

    async def cleanup(self) -> None:
        """Clean up resources when skill shuts down.

        Properly closes database connections and releases resources.
        Called automatically by the BaseSkill lifecycle management.
        """
        # AIDEV-NOTE: Graceful database connection cleanup
        await self.db_engine.dispose()
