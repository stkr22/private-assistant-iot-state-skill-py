"""Template rendering tests for the IoT State Skill.

Validates Jinja2 template output across different scenarios including
single/multiple devices, different state filters, and empty results.
"""

import jinja2
import pytest

from private_assistant_iot_state_skill.iot_state_skill import Action, DeviceType, Parameters, StateFilter


@pytest.fixture(scope="module")
def template_env() -> jinja2.Environment:
    """Create Jinja2 template environment with skill templates."""
    return jinja2.Environment(loader=jinja2.PackageLoader("private_assistant_iot_state_skill", "templates"))


def render_template(
    template_name: str,
    params: Parameters,
    template_env: jinja2.Environment,
) -> str:
    """Render a template with given parameters.

    Args:
        template_name: Name of template file (e.g., "state_query.j2")
        params: Parameters object with query results
        template_env: Jinja2 environment with loaded templates

    Returns:
        Rendered template as string
    """
    template = template_env.get_template(template_name)
    return template.render(params=params)


@pytest.mark.parametrize(
    ("params", "expected_content"),
    [
        pytest.param(
            Parameters(
                action=Action.STATE_QUERY,
                device_type=DeviceType.WINDOW,
                rooms=["living room"],
                state_filter=StateFilter.OPEN,
                states=[("left window", "living_room", "open")],
            ),
            "The left window in room living room is open.",
            id="single_open_window",
        ),
        pytest.param(
            Parameters(
                action=Action.STATE_QUERY,
                device_type=DeviceType.WINDOW,
                rooms=["bedroom"],
                state_filter=StateFilter.CLOSED,
                states=[("right window", "bedroom", "closed")],
            ),
            "The right window in room bedroom is closed.",
            id="single_closed_window",
        ),
        pytest.param(
            Parameters(
                action=Action.STATE_QUERY,
                device_type=DeviceType.WINDOW,
                rooms=["living room", "bedroom"],
                state_filter=StateFilter.ALL,
                states=[
                    ("left window", "living_room", "open"),
                    ("right window", "bedroom", "closed"),
                ],
            ),
            "The left window in room living room is open.\nThe right window in room bedroom is closed.",
            id="multiple_windows_all_states",
        ),
        pytest.param(
            Parameters(
                action=Action.STATE_QUERY,
                device_type=DeviceType.WINDOW,
                rooms=["office"],
                state_filter=StateFilter.OPEN,
                states=[],
            ),
            "No database entries were found for office.",
            id="no_windows_found_with_room",
        ),
        pytest.param(
            Parameters(
                action=Action.STATE_QUERY,
                device_type=DeviceType.WINDOW,
                rooms=["kitchen", "bathroom"],
                state_filter=StateFilter.CLOSED,
                states=[],
            ),
            "No database entries were found for kitchen, bathroom.",
            id="no_windows_found_multiple_rooms",
        ),
    ],
)
def test_state_query_template(
    params: Parameters,
    expected_content: str,
    template_env: jinja2.Environment,
) -> None:
    """Test state_query.j2 template rendering with various scenarios.

    Validates template output for:
    - Single device with open state
    - Single device with closed state
    - Multiple devices across rooms
    - No matching devices (single room)
    - No matching devices (multiple rooms)
    """
    result = render_template("state_query.j2", params, template_env)
    assert result.strip() == expected_content.strip()


@pytest.mark.parametrize(
    ("states", "expected_device_count"),
    [
        pytest.param(
            [("window 1", "room1", "open")],
            1,
            id="single_device",
        ),
        pytest.param(
            [("window 1", "room1", "open"), ("window 2", "room2", "closed"), ("window 3", "room3", "open")],
            3,
            id="three_devices",
        ),
        pytest.param(
            [
                ("window 1", "room1", "open"),
                ("window 2", "room1", "closed"),
                ("window 3", "room2", "open"),
                ("window 4", "room3", "closed"),
            ],
            4,
            id="four_devices_multiple_rooms",
        ),
    ],
)
def test_state_query_template_device_count(
    states: list[tuple[str, str, str]],
    expected_device_count: int,
    template_env: jinja2.Environment,
) -> None:
    """Test state_query.j2 template renders correct number of device entries.

    Validates that the template correctly iterates over all device states
    and generates output for each one.
    """
    params = Parameters(
        action=Action.STATE_QUERY,
        device_type=DeviceType.WINDOW,
        rooms=["test_room"],
        state_filter=StateFilter.ALL,
        states=states,
    )

    result = render_template("state_query.j2", params, template_env)

    # Count lines (each device generates one line)
    lines = [line for line in result.strip().split("\n") if line.strip()]
    assert len(lines) == expected_device_count


def test_state_query_template_empty_states_no_room(
    template_env: jinja2.Environment,
) -> None:
    """Test state_query.j2 template with empty states and no room filter.

    When no rooms are specified in the query, the template should not
    include room information in the "no entries found" message.
    """
    params = Parameters(
        action=Action.STATE_QUERY,
        device_type=DeviceType.WINDOW,
        rooms=[],
        state_filter=StateFilter.ALL,
        states=[],
    )

    result = render_template("state_query.j2", params, template_env)
    assert result.strip() == "No database entries were found."
    assert "for" not in result  # Should not include "for <rooms>"


def test_state_query_template_actual_device_states(
    template_env: jinja2.Environment,
) -> None:
    """Test state_query.j2 template includes actual device state from payload.

    Validates that the template correctly displays the actual device state
    (open/closed) from the database payload, not the query filter.
    """
    # Test with open window
    params_open = Parameters(
        action=Action.STATE_QUERY,
        device_type=DeviceType.WINDOW,
        rooms=["living room"],
        state_filter=StateFilter.OPEN,
        states=[("window 1", "living_room", "open")],
    )
    result_open = render_template("state_query.j2", params_open, template_env)
    assert "is open" in result_open

    # Test with closed window
    params_closed = Parameters(
        action=Action.STATE_QUERY,
        device_type=DeviceType.WINDOW,
        rooms=["bedroom"],
        state_filter=StateFilter.CLOSED,
        states=[("window 2", "bedroom", "closed")],
    )
    result_closed = render_template("state_query.j2", params_closed, template_env)
    assert "is closed" in result_closed

    # Test with ALL filter but mixed actual states
    params_mixed = Parameters(
        action=Action.STATE_QUERY,
        device_type=DeviceType.WINDOW,
        rooms=["kitchen"],
        state_filter=StateFilter.ALL,
        states=[("window 3", "kitchen", "open"), ("window 4", "kitchen", "closed")],
    )
    result_mixed = render_template("state_query.j2", params_mixed, template_env)
    assert "is open" in result_mixed
    assert "is closed" in result_mixed
