FROM python:3.12-slim
RUN groupadd --gid 10001 app && useradd --create-home --uid 10001 --gid app app

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY packages ./packages
COPY services/lead-service/src ./services/lead-service/src
COPY infrastructure/postgres/seed ./infrastructure/postgres/seed
COPY infrastructure/postgres/alembic ./infrastructure/postgres/alembic

RUN pip install --no-cache-dir \
    "alembic>=1.14,<2" \
    "asyncpg>=0.30,<1" \
    "fastapi>=0.121,<1" \
    "sqlalchemy[asyncio]>=2.0.36,<3" \
    "uvicorn[standard]>=0.34,<1"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/packages/configuration:/app/packages/database:/app/packages/contracts:/app/services/lead-service/src

USER app
CMD ["uvicorn", "lead_service.app:app", "--host", "0.0.0.0", "--port", "8001"]
