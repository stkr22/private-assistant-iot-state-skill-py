"""Jinja2 templates for IoT State Skill responses.

This package contains Jinja2 templates used to generate natural language
responses for IoT device state queries. Templates are loaded dynamically
based on available skill actions.

Template Naming Convention:
    Templates follow the pattern: {action_name}.j2

Example:
    - state_query.j2 -> handles STATE_QUERY actions

Available Templates:
    - state_query.j2: Formats device state information for user responses

"""
