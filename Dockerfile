FROM node:24-bookworm-slim@sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530 AS dashboard
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm test && npm run build

FROM python:3.11-slim@sha256:61e01f72680f86a5279ad510818ab7b2ee9f049822589d4e34e33e044360356c AS dependencies
WORKDIR /app
RUN pip install --no-cache-dir --disable-pip-version-check uv==0.13.10
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.11-slim@sha256:61e01f72680f86a5279ad510818ab7b2ee9f049822589d4e34e33e044360356c
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RADAR_ARTIFACTS=/app/artifacts RADAR_FRONTEND=/app/dashboard \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=dashboard /build/dist/carteira-radar/browser /app/dashboard
COPY carteira_radar/ /app/carteira_radar/
USER 65532:65532
EXPOSE 8084
CMD ["uvicorn","carteira_radar.api:app","--host","0.0.0.0","--port","8084","--no-access-log","--limit-concurrency","24"]
