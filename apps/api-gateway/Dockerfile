FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY packages ./packages
COPY apps/api-gateway/src ./apps/api-gateway/src

RUN pip install --no-cache-dir \
    "fastapi>=0.121,<1" \
    "httpx>=0.28,<1" \
    "uvicorn[standard]>=0.34,<1"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/packages/configuration:/app/packages/contracts:/app/apps/api-gateway/src

CMD ["uvicorn", "api_gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
