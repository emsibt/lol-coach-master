# Backend image: FastAPI app (engine + knowledge + workflow + agent + observability).
#
#   docker build -t lol-coach-backend .
#   docker run --rm -p 8000:8000 -v lol-coach-data:/data \
#     -e OPENAI_API_KEY=... lol-coach-backend
#
# The knowledge graph (schema + seed) is packaged with the wheel, so the image
# boots with a working graph. The OP.GG snapshot is runtime data and lives on the
# mounted /data volume: fetch it once, either on the host
# (`python -m lolcoach.cli fetch && ... build-kg`) or inside the container.
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LOL_COACH_DATA_DIR=/data

WORKDIR /app

# Dependencies first (cached layer), then the package itself.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown app:app /data

USER app

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status == 200 else 1)"

CMD ["uvicorn", "lolcoach.main:app", "--host", "0.0.0.0", "--port", "8000"]
