"""Integration tests for the IoT State Skill.

End-to-end tests validating the complete workflow including:
- MQTT message subscription and publishing
- Intent processing through the skill
- Database queries against real PostgreSQL and TimescaleDB
- MQTT response publication

These tests run the skill as a background service with real MQTT broker communication.
"""

import asyncio
import contextlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import aiomqtt
import jinja2
import mqtt_ingest_pipeline.iot_data_transformer as iot_models
import pytest
from private_assistant_commons import MqttConfig, SkillConfig, create_skill_engine, messages
from private_assistant_commons.intent.models import ClassifiedIntent, Entity, EntityType, IntentRequest, IntentType
from private_assistant_commons.mqtt_connection_handler import mqtt_connection_handler
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from private_assistant_iot_state_skill.config import TimescalePostgresConfig
from private_assistant_iot_state_skill.iot_state_skill import IoTStateSkill

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(scope="function")
async def assistant_db_engine() -> AsyncIterator[AsyncEngine]:
    """Create async engine for assistant database with table creation/cleanup."""
    engine = create_skill_engine(echo=False)

    # Create all tables needed by private-assistant-commons
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    # Cleanup: drop all tables
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

    await engine.dispose()


@pytest.fixture(scope="function")
async def iot_db_engine() -> AsyncIterator[AsyncEngine]:
    """Create async engine for IoT TimescaleDB with table creation/cleanup."""
    engine = create_async_engine(
        str(TimescalePostgresConfig().connection_string_async),
        echo=False,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={"command_timeout": 60},
    )

    # Create IoTData table in TimescaleDB
    async with engine.begin() as conn:
        await conn.run_sync(iot_models.IoTData.metadata.create_all)

    yield engine

    # Cleanup: drop IoTData table
    async with engine.begin() as conn:
        await conn.run_sync(iot_models.IoTData.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def assistant_db_session(assistant_db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Provide async database session for assistant database."""
    async with AsyncSession(assistant_db_engine) as session:
        yield session


@pytest.fixture
async def iot_db_session(iot_db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Provide async database session for IoT TimescaleDB."""
    async with AsyncSession(iot_db_engine) as session:
        yield session


@pytest.fixture
def mqtt_config() -> tuple[str, int]:
    """Load MQTT configuration from environment variables."""
    return (
        os.environ.get("MQTT_SERVER_HOST", "localhost"),
        int(os.environ.get("MQTT_SERVER_PORT", "1883")),
    )


@pytest.fixture
def skill_config() -> SkillConfig:
    """Create skill configuration for testing."""
    return SkillConfig(
        client_id="test_iot_state_skill",
    )


@pytest.fixture
def template_env() -> jinja2.Environment:
    """Create Jinja2 template environment for skill."""
    return jinja2.Environment(loader=jinja2.PackageLoader("private_assistant_iot_state_skill", "templates"))


@pytest.fixture
async def mqtt_client(mqtt_config: tuple[str, int]) -> AsyncIterator[aiomqtt.Client]:
    """Create and connect MQTT client for testing.

    Provides a real MQTT client connected to the test broker for publishing
    test messages and subscribing to skill responses.
    """
    hostname, port = mqtt_config
    async with aiomqtt.Client(
        hostname=hostname,
        port=port,
    ) as client:
        yield client


@pytest.fixture
async def running_skill(
    skill_config: SkillConfig,
    mqtt_config: tuple[str, int],
    template_env: jinja2.Environment,
    assistant_db_engine: AsyncEngine,
    iot_db_engine: AsyncEngine,
) -> AsyncIterator[asyncio.Task]:
    """Start the IoT State Skill as a background task.

    The skill runs with real MQTT communication and database connections,
    processing IntentRequest messages from MQTT and publishing responses.
    """
    hostname, port = mqtt_config
    # AIDEV-NOTE: Start skill with mqtt_connection_handler (same as production)
    skill_task = asyncio.create_task(
        mqtt_connection_handler(
            IoTStateSkill,
            skill_config,
            MqttConfig(host=hostname, port=port),
            5,  # reconnection_interval
            template_env=template_env,
            assistant_engine=assistant_db_engine,
            iot_db_engine=iot_db_engine,
        )
    )

    # Give the skill time to start up and connect to MQTT
    await asyncio.sleep(0.5)

    yield skill_task

    # Cleanup: cancel the skill task
    skill_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await skill_task


@pytest.fixture
async def test_iot_data_open_window(iot_db_session: AsyncSession) -> AsyncIterator[iot_models.IoTData]:
    """Create test IoT data entry for an open window sensor."""
    data = iot_models.IoTData(
        device_id="window_sensor_living_room",
        device_name="left window",
        device_type="window_sensor",
        room="living_room",
        time=datetime.now(),  # AIDEV-NOTE: Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE
        topic="zigbee2mqtt/window_sensor_living_room",
        payload={"contact": False},  # False = open
    )
    data_2 = iot_models.IoTData(
        device_id="window_sensor_kitchen",
        device_name="right window",
        device_type="window_sensor",
        room="kitchen",
        time=datetime.now(),  # AIDEV-NOTE: Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE
        topic="zigbee2mqtt/window_sensor_kitchen",
        payload={"contact": False},  # False = open
    )
    iot_db_session.add(data)
    iot_db_session.add(data_2)
    await iot_db_session.commit()
    await iot_db_session.refresh(data)
    await iot_db_session.refresh(data_2)
    yield data
    # Cleanup after test
    await iot_db_session.delete(data)
    await iot_db_session.delete(data_2)
    await iot_db_session.commit()


@pytest.fixture
async def test_iot_data_closed_window(iot_db_session: AsyncSession) -> AsyncIterator[iot_models.IoTData]:
    """Create test IoT data entry for a closed window sensor."""
    data = iot_models.IoTData(
        device_id="window_sensor_bedroom",
        device_name="right window",
        device_type="window_sensor",
        room="bedroom",
        time=datetime.now(),  # AIDEV-NOTE: Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE
        topic="zigbee2mqtt/window_sensor_bedroom",
        payload={"contact": True},  # True = closed
    )
    iot_db_session.add(data)
    await iot_db_session.commit()
    await iot_db_session.refresh(data)
    yield data
    # Cleanup after test
    await iot_db_session.delete(data)
    await iot_db_session.commit()


@pytest.fixture
async def test_iot_data_kitchen_window(iot_db_session: AsyncSession) -> AsyncIterator[iot_models.IoTData]:
    """Create test IoT data entry for a kitchen window sensor."""
    data = iot_models.IoTData(
        device_id="window_sensor_kitchen",
        device_name="back window",
        device_type="window_sensor",
        room="kitchen",
        time=datetime.now(),  # AIDEV-NOTE: Use timezone-naive datetime for TIMESTAMP WITHOUT TIME ZONE
        topic="zigbee2mqtt/window_sensor_kitchen",
        payload={"contact": False},  # False = open
    )
    iot_db_session.add(data)
    await iot_db_session.commit()
    await iot_db_session.refresh(data)
    yield data
    # Cleanup after test
    await iot_db_session.delete(data)
    await iot_db_session.commit()


@pytest.fixture
async def test_iot_data_old_entry(iot_db_session: AsyncSession) -> AsyncIterator[iot_models.IoTData]:
    """Create old IoT data entry (should be filtered out by 2-day window)."""
    data = iot_models.IoTData(
        device_id="window_sensor_garage",
        device_name="garage window",
        device_type="window_sensor",
        room="garage",
        time=datetime.now() - timedelta(days=3),  # 3 days old, timezone-naive
        topic="zigbee2mqtt/window_sensor_garage",
        payload={"contact": False},
    )
    iot_db_session.add(data)
    await iot_db_session.commit()
    await iot_db_session.refresh(data)
    yield data
    # Cleanup after test
    await iot_db_session.delete(data)
    await iot_db_session.commit()


async def publish_intent_request(
    mqtt_client: aiomqtt.Client,
    intent_request: IntentRequest,
) -> None:
    """Publish an IntentRequest to the skill's input topic via MQTT.

    Args:
        mqtt_client: Connected MQTT client
        intent_request: Intent request to publish
    """
    # AIDEV-NOTE: Publish to the skill's input topic (assistant/intent_engine/result)
    await mqtt_client.publish(
        "assistant/intent_engine/result",
        payload=intent_request.model_dump_json(),
    )


async def wait_for_response(
    mqtt_client: aiomqtt.Client,
    output_topic: str,
    timeout: float = 5.0,
) -> str:
    """Subscribe to output topic and wait for skill response.

    Args:
        mqtt_client: Connected MQTT client
        output_topic: Topic to subscribe to for responses
        timeout: Maximum time to wait for response in seconds

    Returns:
        Response text from the skill

    Raises:
        asyncio.TimeoutError: If no response received within timeout
    """
    # AIDEV-NOTE: Subscribe to the output topic before publishing
    await mqtt_client.subscribe(output_topic)

    try:
        async with asyncio.timeout(timeout):
            async for message in mqtt_client.messages:
                if message.topic.matches(output_topic):
                    # Parse the ServerResponse from MQTT message
                    payload = message.payload
                    if isinstance(payload, bytes):
                        response_data = json.loads(payload.decode())
                        return str(response_data.get("text", ""))
                    msg = f"Unexpected payload type: {type(payload)}"
                    raise TypeError(msg)
            # This should never be reached as messages is an infinite async iterator
            raise RuntimeError("MQTT message stream ended unexpectedly")
    except TimeoutError as e:
        msg = f"No response received on topic {output_topic} within {timeout}s"
        raise TimeoutError(msg) from e


class TestQueryStatusIntent:
    """Test QUERY_STATUS intent with end-to-end MQTT flow."""

    @pytest.mark.asyncio
    async def test_query_all_windows(
        self,
        mqtt_client: aiomqtt.Client,
        running_skill: asyncio.Task,  # noqa: ARG002
        test_iot_data_open_window: iot_models.IoTData,  # noqa: ARG002
        test_iot_data_closed_window: iot_models.IoTData,  # noqa: ARG002
    ) -> None:
        """Test querying all window states in a room via MQTT."""
        output_topic = "assistant/living_room/output"

        # AIDEV-NOTE: Subscribe to output topic before sending request
        await mqtt_client.subscribe(output_topic)

        # AIDEV-NOTE: Create and publish intent request via MQTT
        client_request = messages.ClientRequest(
            id=uuid.uuid4(),
            text="Show me all windows in the living room",
            room="kitchen",
            output_topic=output_topic,
        )
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.QUERY_STATUS,
            confidence=0.9,
            entities={
                "device": [
                    Entity(
                        type=EntityType.DEVICE,
                        raw_text="windows",
                        normalized_value="windows",
                        confidence=0.95,
                    )
                ],
                "room": [Entity(type=EntityType.ROOM, raw_text="living room", normalized_value="living room")],
            },
            raw_text="Show me all windows in the living room",
        )
        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        await publish_intent_request(mqtt_client, intent_request)

        # AIDEV-NOTE: Wait for response via MQTT
        response_text = await wait_for_response(mqtt_client, output_topic, timeout=5.0)

        assert response_text.strip() == "The left window in room living room is open."

    @pytest.mark.asyncio
    async def test_query_open_windows_only(
        self,
        mqtt_client: aiomqtt.Client,
        running_skill: asyncio.Task,  # noqa: ARG002
        test_iot_data_open_window: iot_models.IoTData,  # noqa: ARG002
        test_iot_data_closed_window: iot_models.IoTData,  # noqa: ARG002
    ) -> None:
        """Test querying only open windows using state filter via MQTT."""
        output_topic = "assistant/living_room/output"

        await mqtt_client.subscribe(output_topic)

        # AIDEV-NOTE: Create intent request for "are there any open windows"
        client_request = messages.ClientRequest(
            id=uuid.uuid4(),
            text="Are there any open windows in the living room?",
            room="living room",
            output_topic=output_topic,
        )
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.QUERY_STATUS,
            confidence=0.9,
            entities={
                "device": [
                    Entity(
                        type=EntityType.DEVICE,
                        raw_text="windows",
                        normalized_value="windows",
                        confidence=0.95,
                    )
                ],
                "room": [Entity(type=EntityType.ROOM, raw_text="living room", normalized_value="living room")],
            },
            raw_text="Are there any open windows in the living room?",
        )
        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        await publish_intent_request(mqtt_client, intent_request)
        response_text = await wait_for_response(mqtt_client, output_topic, timeout=5.0)

        # AIDEV-NOTE: Verify response contains only open windows
        assert response_text.strip() == "The left window in room living room is open."

    @pytest.mark.asyncio
    async def test_query_closed_windows_only(
        self,
        mqtt_client: aiomqtt.Client,
        running_skill: asyncio.Task,  # noqa: ARG002
        test_iot_data_open_window: iot_models.IoTData,  # noqa: ARG002
        test_iot_data_closed_window: iot_models.IoTData,  # noqa: ARG002
    ) -> None:
        """Test querying only closed windows using state filter via MQTT."""
        output_topic = "assistant/bedroom/output"

        await mqtt_client.subscribe(output_topic)

        # AIDEV-NOTE: Create intent request for "show me closed windows"
        client_request = messages.ClientRequest(
            id=uuid.uuid4(),
            text="Show me closed windows in the bedroom",
            room="bedroom",
            output_topic=output_topic,
        )
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.QUERY_STATUS,
            confidence=0.9,
            entities={
                "device": [
                    Entity(
                        type=EntityType.DEVICE,
                        raw_text="windows",
                        normalized_value="windows",
                        confidence=0.95,
                    )
                ],
                "room": [
                    Entity(type=EntityType.ROOM, raw_text="bedroom", normalized_value="bedroom"),
                ],
            },
            raw_text="Show me closed windows in the bedroom",
        )
        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        await publish_intent_request(mqtt_client, intent_request)
        response_text = await wait_for_response(mqtt_client, output_topic, timeout=5.0)

        # AIDEV-NOTE: Verify response contains only closed windows
        assert response_text.strip() == "The right window in room bedroom is closed."

    @pytest.mark.asyncio
    async def test_query_windows_multi_room(
        self,
        mqtt_client: aiomqtt.Client,
        running_skill: asyncio.Task,  # noqa: ARG002
        test_iot_data_open_window: iot_models.IoTData,  # noqa: ARG002
        test_iot_data_kitchen_window: iot_models.IoTData,  # noqa: ARG002
    ) -> None:
        """Test querying windows across multiple rooms via MQTT."""
        output_topic = "assistant/bedroom/output"

        await mqtt_client.subscribe(output_topic)

        # AIDEV-NOTE: Create intent request for "show me all windows in multiple rooms"
        client_request = messages.ClientRequest(
            id=uuid.uuid4(),
            text="Show me all windows in living room and kitchen",
            room="bedroom",
            output_topic=output_topic,
        )
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.QUERY_STATUS,
            confidence=0.9,
            entities={
                "device": [
                    Entity(
                        type=EntityType.DEVICE,
                        raw_text="windows",
                        normalized_value="windows",
                        confidence=0.95,
                    )
                ],
                "room": [
                    Entity(type=EntityType.ROOM, raw_text="kitchen", normalized_value="kitchen"),
                    Entity(type=EntityType.ROOM, raw_text="living room", normalized_value="living room"),
                ],
            },
            raw_text="Show me all windows in living room and kitchen",
        )
        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        await publish_intent_request(mqtt_client, intent_request)
        response_text = await wait_for_response(mqtt_client, output_topic, timeout=5.0)

        # AIDEV-NOTE: Verify response contains windows from both rooms
        # Template outputs each device on a separate line
        expected_lines = [
            "The left window in room living room is open.",
            "The back window in room kitchen is open.",
        ]
        actual_lines = sorted([line.strip() for line in response_text.strip().split("\n") if line.strip()])
        expected_lines_sorted = sorted(expected_lines)
        assert actual_lines == expected_lines_sorted

    @pytest.mark.asyncio
    async def test_query_no_matching_windows(
        self,
        mqtt_client: aiomqtt.Client,
        running_skill: asyncio.Task,  # noqa: ARG002
    ) -> None:
        """Test query when no windows match the criteria via MQTT."""
        output_topic = "assistant/garage/output"

        await mqtt_client.subscribe(output_topic)

        # AIDEV-NOTE: Create intent request for non-existent room
        client_request = messages.ClientRequest(
            id=uuid.uuid4(),
            text="Show me windows in the garage",
            room="garage",
            output_topic=output_topic,
        )
        classified_intent = ClassifiedIntent(
            id=uuid.uuid4(),
            intent_type=IntentType.QUERY_STATUS,
            confidence=0.9,
            entities={
                "device": [
                    Entity(
                        type=EntityType.DEVICE,
                        raw_text="windows",
                        normalized_value="windows",
                        confidence=0.95,
                    )
                ],
                "room": [Entity(type=EntityType.ROOM, raw_text="garage", normalized_value="garage")],
            },
            raw_text="Show me windows in the garage",
        )
        intent_request = IntentRequest(
            id=uuid.uuid4(),
            classified_intent=classified_intent,
            client_request=client_request,
        )

        await publish_intent_request(mqtt_client, intent_request)
        response_text = await wait_for_response(mqtt_client, output_topic, timeout=5.0)

        # AIDEV-NOTE: Verify "no entries found" response
        assert response_text.strip() == "No database entries were found for garage."
