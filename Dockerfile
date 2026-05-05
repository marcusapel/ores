# ── Build stage: install Python deps into a virtual-env ──────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────
FROM python:3.12-slim

# Security: non-root user (numeric UID required by Radix runAsNonRoot policy)
RUN groupadd -g 1001 ores && useradd -u 1001 -g 1001 -r -d /app -s /sbin/nologin ores

WORKDIR /app

# Copy virtual-env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy application code
COPY app/          ./app/
COPY demo/         ./demo/
COPY md/           ./md/

# Own everything by the non-root user
RUN mkdir -p /data && chown -R ores:ores /app /data

USER 1001

EXPOSE 8000

# Kubernetes probes can hit GET /login-page (public, no auth required)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/login-page')"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
