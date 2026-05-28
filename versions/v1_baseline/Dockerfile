# syntax=docker/dockerfile:1.6
#
# Backlog Synthesizer — container image.
#
# Defaults to the Streamlit UI on port 8501. To run the CLI synthesizer
# instead, override the entrypoint:
#
#   docker run --rm --env-file .env -v "$PWD/outputs:/app/outputs" \
#     backlog-synthesizer:latest \
#     python src/main.py \
#       --transcript samples/meeting_notes.txt \
#       --constraints samples/architecture_constraints.md \
#       --backlog samples/jira_backlog.json
#
# Build:  docker build -t backlog-synthesizer:latest .
# Run UI: docker run -d -p 8501:8501 --env-file .env backlog-synthesizer:latest
# Health: curl -f http://localhost:8501/_stcore/health

FROM python:3.11-slim-bookworm

# .pyc files only waste image space; unbuffered stdout makes `docker logs`
# show output as the run progresses instead of dribbling out at the end.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# build-essential is needed by some transitive deps that compile from
# source; curl is used by HEALTHCHECK; libgomp1 is required by
# sentence-transformers / numpy on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer is cached when only app code changes.
#
# CPU-only PyTorch is installed BEFORE requirements.txt: sentence-transformers
# depends on torch, and the default index serves a ~3 GB CUDA-enabled wheel
# even though we never run on a GPU. Forcing the CPU wheel first means pip
# sees torch already satisfied when it processes requirements.txt — image
# size drops from ~3 GB to ~1 GB.
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r requirements.txt

# Application code. Mirror these paths in .dockerignore for build-context
# size, but this explicit COPY is the actual guarantee.
COPY app.py ./
COPY src/ ./src/
COPY prompts/ ./prompts/
COPY samples/ ./samples/
COPY evaluation/ ./evaluation/

# Pre-create runtime dirs so they exist with non-root ownership.
RUN mkdir -p outputs logs

# Non-root user. Streamlit doesn't need root, and root-in-a-container is a
# known security smell.
RUN useradd --create-home --uid 1000 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

# Streamlit's /_stcore/health endpoint is the cleanest liveness probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "app.py", \
    "--server.port=8501", \
    "--server.address=0.0.0.0"]
