FROM python:3.12-slim

ENV PYTHONUNBUFFERED 1

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:0.5.9 /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -s /sbin/nologin -M appuser

# Copy the application into the container.
COPY pyproject.toml README.md uv.lock /app/
COPY src /app/src
RUN uv sync --frozen --no-cache

CMD ["/app/.venv/bin/private_assistant_iot_state_skill"]