# ---- Stage 1: Builder ----
FROM python:3.14-slim AS builder

WORKDIR /build

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .

COPY app/ app/

# ---- Stage 2: Production ----
FROM python:3.14-slim AS runner

RUN groupadd -r appuser --gid 10001 \
    && useradd -r -g appuser --uid 10001 --create-home appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/app/ app/

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import httpx; r = httpx.get('http://localhost:8000/v1/healthz'); r.raise_for_status()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
