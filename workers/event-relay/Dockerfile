FROM python:3.12-slim
RUN groupadd --gid 10001 app && useradd --create-home --uid 10001 --gid app app
WORKDIR /app
RUN pip install --no-cache-dir "redis>=5.2,<6" "pydantic>=2.9,<3" \
    "sqlalchemy[asyncio]>=2.0.36,<3" "asyncpg>=0.30,<1"
COPY packages/contracts ./packages/contracts
COPY packages/configuration ./packages/configuration
COPY packages/database ./packages/database
COPY packages/event-bus ./packages/event-bus
COPY workers/event-relay/src ./workers/event-relay/src
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/packages/configuration:/app/packages/contracts:/app/packages/database:/app/packages/event-bus:/app/workers/event-relay/src
USER app
CMD ["python", "-m", "event_relay", "run"]
