# syntax=docker/dockerfile:1.7

FROM node:20-alpine AS css
WORKDIR /app
COPY package.json tailwind.config.js ./
COPY marketpulse/web/static/app.src.css ./marketpulse/web/static/app.src.css
COPY marketpulse/web/templates ./marketpulse/web/templates
RUN npm install && \
    npx tailwindcss \
      -i marketpulse/web/static/app.src.css \
      -o marketpulse/web/static/app.css \
      --minify

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY marketpulse ./marketpulse
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
COPY --from=css /app/marketpulse/web/static/app.css ./marketpulse/web/static/app.css

RUN useradd -u 1001 -m app && chown -R app /app
USER app

ENV DATABASE_URL=sqlite:////data/marketpulse.db
EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn marketpulse.web.main:app --host 0.0.0.0 --port 8000"]
