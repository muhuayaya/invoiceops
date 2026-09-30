FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app
RUN python -m pip install --no-cache-dir uv==0.12.10
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY apps ./apps
COPY .streamlit ./.streamlit
COPY ml ./ml
COPY configs ./configs
COPY infra/migrations ./infra/migrations
RUN uv sync --frozen --no-dev
