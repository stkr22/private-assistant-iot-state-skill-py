"""Core implementation of the Private Assistant IoT State Skill.

This module contains the main skill logic for querying IoT device states
from TimescaleDB, processing natural language intents, and generating
responses through the private assistant framework.
"""

from datetime import datetime, timedelta
from enum import Enum

import jinja2
import mqtt_ingest_pipeline.iot_data_transformer as models
from private_assistant_commons import skill_config
from private_assistant_commons.base_skill import BaseSkill
from private_assistant_commons.intent.models import IntentRequest, IntentType
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

    help_text = "Query IoT device states from your home - check if windows are open or closed in specific rooms"

    def __init__(
        self,
        config_obj: skill_config.SkillConfig,
        template_env: jinja2.Environment,
        assistant_engine: AsyncEngine,
        iot_db_engine: AsyncEngine,
        **kwargs,
    ) -> None:
        """Initialize the IoT State Skill.

        Args:
            config_obj: Skill configuration with database settings
            template_env: Jinja2 environment for template rendering
            assistant_engine: Async database engine for assistant database (device registry)
            iot_db_engine: Async database engine for IoT data queries (TimescaleDB)
            **kwargs: Additional arguments passed to BaseSkill

        """
        super().__init__(config_obj=config_obj, engine=assistant_engine, **kwargs)
        self.iot_db_engine = iot_db_engine
        self.template_env = template_env
        self.action_to_template: dict[Action, jinja2.Template] = {}

        # AIDEV-NOTE: Intent configuration for skill competition
        self.supported_intents = {
            IntentType.DATA_QUERY: 0.8,
        }
        self.supported_device_types = ["window_sensor"]

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

        Called once during skill startup to:
        1. Call parent class preparations (loads devices from global registry)
        2. Register window sensor devices for intent engine
        3. Load Jinja2 templates for response generation

        Part of the BaseSkill lifecycle.
        """
        await super().skill_preparations()

        # AIDEV-NOTE: Register window sensor as queryable device type
        # Device type-driven approach: device_type determines capabilities
        # TODO: Future enhancement - migrate to device capability system (see GitHub issue)
        await self.register_device(
            device_type="window_sensor",
            name="window",
            pattern=["window", "windows"],  # Keywords for entity detection
            room=None,  # Available in all rooms
        )

        self._load_templates()

    def _extract_state_filter_from_text(self, raw_text: str) -> StateFilter:
        """Extract state filter from raw text.

        AIDEV-TODO: Migrate to entity-based extraction when intent engine supports state entities.
        This should come from classified_intent.entities["state"] in the future.
        See GitHub issue for entity-based state extraction enhancement.

        Args:
            raw_text: The raw text from the user's request

        Returns:
            StateFilter: The extracted state filter (OPEN, CLOSED, or ALL)

        """
        text_words = set(raw_text.lower().split())
        if "open" in text_words:
            return StateFilter.OPEN
        if "closed" in text_words:
            return StateFilter.CLOSED
        return StateFilter.ALL

    def get_parameters(self, intent_request: IntentRequest) -> Parameters:
        """Extract query parameters from intent request.

        Analyzes the classified intent to determine:
        - Which device type is being queried (from entities or text fallback)
        - Which rooms to search (from entities or request origin)
        - What state filter to apply (from text parsing for now)

        Args:
            intent_request: Intent request with classified intent and client request

        Returns:
            Parameters: Structured parameters for database query

        Raises:
            ValueError: If no supported device type is found in the request

        """
        classified_intent = intent_request.classified_intent
        client_request = intent_request.client_request

        # AIDEV-NOTE: Try entity-based device detection first, fallback to text-based
        device_entities = classified_intent.entities.get("device", [])
        device_type = None

        if device_entities:
            # Entity-based detection (preferred)
            for entity in device_entities:
                if entity.normalized_value in self.device_type_map:
                    device_type = self.device_type_map[entity.normalized_value]
                    break

        if not device_type:
            # AIDEV-NOTE: Fallback to text-based detection during transition period
            # Extract nouns from raw_text for compatibility
            text_words = classified_intent.raw_text.lower().split()
            device_type = next(
                (self.device_type_map[word] for word in text_words if word in self.device_type_map), None
            )

        if not device_type:
            raise ValueError("No valid device type found in request")

        # AIDEV-NOTE: Room resolution from entities or fallback to request origin
        room_entities = classified_intent.entities.get("room", [])
        rooms = [entity.normalized_value for entity in room_entities] if room_entities else [client_request.room]

        # AIDEV-NOTE: State filter extraction using text-based helper (temporary)
        state_filter = self._extract_state_filter_from_text(classified_intent.raw_text)

        return Parameters(device_type=device_type, rooms=rooms, state_filter=state_filter)

    async def get_device_states(self, params: Parameters) -> list[tuple]:
        """Query TimescaleDB for current device states.

        Retrieves the most recent state for each device matching the query
        parameters. Uses a window function to get the latest reading per device
        and applies state filtering if requested.

        Args:
            params: Query parameters with device type, rooms, and state filter

        Returns:
            list[tuple]: List of (device_name, room, state) tuples matching the query.
                        State is "open" or "closed" based on the payload contact value.

        """
        # AIDEV-NOTE: Room name normalization - database stores rooms with underscores instead of spaces
        rooms_wo_whitespace = [room.replace(" ", "_") for room in params.rooms]

        async with AsyncSession(self.iot_db_engine) as session:
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
            base_query = select(subquery.c.device_name, subquery.c.room, subquery.c.payload)
            if params.state_filter != StateFilter.ALL:
                is_closed = params.state_filter != StateFilter.OPEN
                base_query = base_query.where(subquery.c.payload["contact"].astext == str(is_closed).lower())
            query = base_query.where(subquery.c.row_num == 1)
            result = await session.exec(query)

            # AIDEV-NOTE: Extract actual state from payload and return (device_name, room, state)
            # contact=False means open, contact=True means closed
            states = []
            for device_name, room, payload in result.all():
                is_closed = payload.get("contact", False)
                state = "closed" if is_closed else "open"
                states.append((device_name, room, state))

            return states

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

    async def process_request(self, intent_request: IntentRequest) -> None:
        """Process a user's natural language request about IoT devices.

        Main entry point for handling voice queries. Extracts parameters,
        queries the database, generates a response, and sends it back
        through the MQTT broker.

        Args:
            intent_request: Intent request with classified intent and client request

        """
        # AIDEV-NOTE: Request processing pipeline: params -> query -> response
        params = self.get_parameters(intent_request)
        params.states = await self.get_device_states(params)
        response = self.get_answer(params)
        await self.send_response(response, intent_request.client_request)

    async def cleanup(self) -> None:
        """Clean up resources when skill shuts down.

        Properly closes database connections and releases resources.
        Called automatically by the BaseSkill lifecycle management.
        """
        # AIDEV-NOTE: Graceful database connection cleanup for IoT data engine
        await self.iot_db_engine.dispose()
