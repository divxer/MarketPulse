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
    UV_LINK_MODE=copy

# Install uv into a system-wide location so the non-root `app` user can execute it.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && chmod +x /usr/local/bin/uv /usr/local/bin/uvx

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY marketpulse ./marketpulse
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
COPY --from=css /app/marketpulse/web/static/app.css ./marketpulse/web/static/app.css

RUN useradd -u 1001 -m app && chown -R app /app \
    && mkdir -p /data && chown 1001:1001 /data
USER app

ENV DATABASE_URL=sqlite:////data/marketpulse.db \
    PATH="/app/.venv/bin:${PATH}"
EXPOSE 8000
# Use .venv binaries directly so `uv run` doesn't trigger an implicit sync
# that would re-download dev dependencies at every container start.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn marketpulse.web.main:app --host 0.0.0.0 --port 8000"]
