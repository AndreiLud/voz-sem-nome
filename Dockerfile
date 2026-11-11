# One image for the public API, the internal API, the queue worker and the batch pipeline.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    ENPS_ROOT=/app \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY config ./config
COPY prompts ./prompts
COPY src ./src
COPY data/reference ./data/reference

# Runs as an unprivileged user; compose passes the host UID so mounted files stay yours.
USER 1000:1000

CMD ["python", "-m", "enps.queue.worker"]
