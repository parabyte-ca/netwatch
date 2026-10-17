# ── Stage 1: build the React frontend ─────────────────────────────────────────
FROM node:25-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim

ARG APP_VERSION=0.0.0-dev
LABEL org.opencontainers.image.source="https://github.com/parabyte-ca/netwatch" \
      org.opencontainers.image.description="Self-hosted network diagnostics and root-cause analysis" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    NETWATCH_DATA_DIR=/data \
    NETWATCH_STATIC_DIR=/app/static

WORKDIR /app
COPY backend/ /tmp/backend/
RUN pip install /tmp/backend && rm -rf /tmp/backend
COPY --from=frontend /build/dist /app/static

# Informational only: the container uses host networking and binds NETWATCH_PORT.
EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s --retries=3 \
    CMD ["python", "-m", "netwatch.healthcheck"]

CMD ["python", "-m", "netwatch"]
