# syntax=docker/dockerfile:1

# ---- build stage: resolve dependencies from the lock file, fetch the font ----
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /srv

# Dependencies first, so this layer is cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN uv sync --locked --no-dev
# Self-host Caveat: downloaded once at build time, served from /static at runtime.
RUN python scripts/fetch_fonts.py --if-missing || echo "WARNING: Caveat not downloaded; the site will use a system cursive font"

# ---- runtime stage: no build tools, non-root ----
FROM python:3.12-slim AS runtime
ENV PATH="/srv/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home storyfeta
WORKDIR /srv
COPY --from=build --chown=storyfeta /srv /srv
USER storyfeta

# PORT is read from the environment (default 8000 in app.core.config).
EXPOSE 8000
# Web process. Override the command for the other process types:
#   release: alembic upgrade head        worker: python -m app.worker
CMD ["python", "-m", "app"]
