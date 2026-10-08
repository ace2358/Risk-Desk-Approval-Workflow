FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKFLOW_DATABASE_URL=sqlite:////data/workflow.db

WORKDIR /app
COPY pyproject.toml ./
COPY workflow_engine ./workflow_engine
RUN python -m pip install --no-cache-dir . \
    && useradd --system --create-home app \
    && mkdir /data \
    && chown app:app /data

FROM base AS test
COPY tests ./tests
RUN python -m pip install --no-cache-dir pytest httpx
CMD ["python", "-m", "pytest"]

FROM base AS app
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "workflow_engine.api:app", "--host", "0.0.0.0", "--port", "8000"]