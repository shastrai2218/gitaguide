# ---- GitaGuide API image (CPU only, torch-free) ---------------------------
#
# Installs requirements.txt, which is the **serving** dependency set: both
# models run as ONNX graphs on onnxruntime, so no training framework is
# installed. The image is ~490 MB of site-packages instead of ~5.6 GB (see
# docs/kg_measurements.md section 9). requirements-dev.txt, which does pull
# torch, is deliberately never referenced here.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

WORKDIR /app

# curl is used by the container healthcheck. No build-essential: every wheel in
# the serving set ships a manylinux binary, and dropping the toolchain removes
# ~200 MB and a compiler from the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY corpus ./corpus
COPY kg ./kg
COPY eval ./eval
COPY scripts ./scripts
COPY pytest.ini ./pytest.ini

# Model cache. On Render this is ephemeral, so the first request after a deploy
# pays the ONNX download; the lifespan handler warms both models at startup so
# that cost lands before the health check passes, not on a tester's first turn.
RUN mkdir -p /models

# Telemetry is a rotating local file. On Render it does not survive a restart,
# which is why the durable record is PostgreSQL and this is the convenience
# view. Documented in docs/DEPLOY.md rather than pretended otherwise.
RUN mkdir -p /app/logs

EXPOSE 8000

# Render supplies $PORT; the default keeps `docker run` working unchanged.
ENV PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# Plain uvicorn, single worker, no process manager. Both halves matter.
#
# --workers 1: the rate limiter and telemetry counters are in-process, so a
# second worker would silently double every configured limit. Raising
# concurrency means moving those to shared storage first.
#
# No gunicorn: a turn can legitimately run for well over a minute - a 45s
# generation budget on top of the intent call, retrieval and persistence, and
# the frontend waits 150s before giving up (REQUEST_TIMEOUT_MS in
# frontend/src/api.js). gunicorn's default worker timeout is 30s and it does
# not fail politely: it kills the worker mid-request, which the platform
# surfaces as a 502 with nothing in the application log to explain it. Plain
# uvicorn applies no request deadline of its own, so the pipeline's own budget
# stays the only thing that decides when a turn has taken too long.
#
# If a process manager is ever needed, it must allow at least 150s per request.
# tests/test_deploy_config.py asserts this.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
