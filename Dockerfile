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
# Read at runtime: app/pipeline/router.py loads data/intent_lexicon.yaml. Its
# absence is not a crash - get_lexicon() degrades - so the only symptom was
# every greeting paying a model call. tests/test_deploy_config.py now asserts
# this list covers every PROJECT_ROOT-relative path the image can read.
COPY data ./data

# Model cache. Without a persistent volume this is ephemeral, so the first
# request after a rebuild pays the ONNX download (docker-compose.vm.yml mounts a
# volume here so it survives restarts). The lifespan handler warms both models
# at startup so that cost lands before the health check passes, not on a
# tester's first turn.
RUN mkdir -p /models

# Telemetry is a rotating local file. On a host without a persistent volume it
# does not survive a restart, which is why the durable record is PostgreSQL and
# this is the convenience view.
RUN mkdir -p /app/logs

EXPOSE 8000

# A platform may supply $PORT; the default keeps `docker run` working unchanged.
ENV PORT=8000

# Two memory settings, both measured under the free tier's 512 MB on 7 Sep 2026.
#
# MALLOC_ARENA_MAX=2: glibc gives each thread its own malloc arena, and the
# resident cost of those arenas is pure overhead for a single-worker service.
# Capping it saved 14.5 MiB (459.8 -> 445.3 idle).
#
# One ONNX thread: the free instance has 0.1 CPU, so a second intra-op thread
# cannot run in parallel with the first - it only adds an arena and contention.
# Dropping to one saved a further 47.1 MiB (445.3 -> 398.2 idle) and made the
# cold start *faster* (105s -> 80s).
ENV MALLOC_ARENA_MAX=2 \
    OMP_NUM_THREADS=1

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
