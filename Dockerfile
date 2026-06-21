# syntax=docker/dockerfile:1.6
#
# Backlog Synthesizer — multi-stage container image.
#
# Stage 1 (builder): installs build tools + compiles all wheels.
# Stage 2 (runtime): copies only the installed packages — no compiler toolchain.
#
# Defaults to the Streamlit UI on port 8501.
#
# Build:  docker build -t backlog-synthesizer:latest .
# Run UI: docker run -d -p 8501:8501 --env-file .env backlog-synthesizer:latest
# Health: curl -f http://localhost:8501/_stcore/health

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential is needed by some transitive deps that compile from source.
# It lives only in this builder stage — the final image never sees it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt requirements-lock.txt ./

# CPU-only PyTorch is installed first to save ~2 GB of container image space
RUN pip install --upgrade pip \
 && pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install --prefix=/install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    HF_HOME=/app/.cache \
    # Tell Python where the builder-stage packages landed.
    PYTHONPATH=/usr/local/lib/python3.11/site-packages

# curl: required by HEALTHCHECK.
# libgomp1: required by sentence-transformers / numpy on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the compiled packages from the builder stage (no compiler in final image).
COPY --from=builder /install /usr/local

WORKDIR /app

# Application code.
COPY app.py ./
COPY entrypoint.sh ./
COPY .streamlit/ ./.streamlit/
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY samples/ ./samples/
COPY evaluation/ ./evaluation/
COPY config/ ./config/

# Bake the sentence-transformers embedding model into the image layer so the
# first synthesis has zero cold-start delay in the "detecting duplicates" stage.
RUN python src/warmup.py

# Pre-create runtime dirs so they exist with non-root ownership.
RUN mkdir -p outputs logs .cache

# Non-root user — Streamlit doesn't need root.
RUN useradd --create-home --uid 1000 appuser \
 && chown -R appuser:appuser /app \
 && chmod +x /app/entrypoint.sh
USER appuser

EXPOSE 8501

# Tell the container runtime which signal to send on `docker stop`
STOPSIGNAL SIGTERM

# Streamlit's /_stcore/health endpoint is the cleanest liveness probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl --fail --silent http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
