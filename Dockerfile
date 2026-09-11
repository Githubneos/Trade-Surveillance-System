# syntax=docker/dockerfile:1

# ---- frontend build -----------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build
# Copy manifests first so npm ci is cached independently of source changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- python base --------------------------------------------------------------------
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# libpq for psycopg; build-essential is NOT installed because every dependency ships
# manylinux wheels for this platform, which keeps the image small and the build fast.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY surveillance/ ./surveillance/
RUN pip install --no-cache-dir -e .

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY --from=frontend /build/dist ./frontend/dist

# /app/data must EXIST in the image, not just be a mount point. Docker seeds a fresh named
# volume from the image directory it is mounted over -- including ownership -- so without
# this the volume is created root-owned and the non-root process cannot write its dataset.
RUN mkdir -p /app/data

# Run as a non-root user: a container that does not need root should not have it.
RUN useradd --create-home --uid 10001 surveil && chown -R surveil:surveil /app
USER surveil

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=40s --retries=5 \
  CMD curl -fsS http://localhost:8000/api/stats || exit 1

CMD ["python", "-m", "uvicorn", "surveillance.api.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
