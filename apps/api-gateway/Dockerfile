FROM python:3.12-slim
RUN groupadd --gid 10001 app && useradd --create-home --uid 10001 --gid app app

WORKDIR /app

COPY pyproject.toml ./
COPY packages ./packages
COPY apps/api-gateway/src ./apps/api-gateway/src

RUN pip install --no-cache-dir \
    "fastapi>=0.121,<1" \
    "httpx>=0.28,<1" \
    "uvicorn[standard]>=0.34,<1"

ARG INSTALL_VOICE_RUNTIME=0
RUN if [ "$INSTALL_VOICE_RUNTIME" = "1" ]; then \
    pip install --no-cache-dir "pipecat-ai[webrtc,silero]==1.12.0"; \
    fi

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/packages/configuration:/app/packages/contracts:/app/packages/llm:/app/packages/speech:/app/packages/voice-runtime:/app/apps/api-gateway/src

USER app
CMD ["uvicorn", "api_gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
