FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY packages ./packages
COPY services/conversation-service/src ./services/conversation-service/src
COPY infrastructure/postgres/alembic ./infrastructure/postgres/alembic

RUN pip install --no-cache-dir \
    "asyncpg>=0.30,<1" \
    "fastapi>=0.121,<1" \
    "httpx>=0.28,<1" \
    "sqlalchemy[asyncio]>=2.0.36,<3" \
    "uvicorn[standard]>=0.34,<1"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/packages/configuration:/app/packages/database:/app/packages/contracts:/app/services/conversation-service/src

CMD ["uvicorn", "conversation_service.app:app", "--host", "0.0.0.0", "--port", "8002"]
