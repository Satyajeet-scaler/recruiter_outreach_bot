# Production image for Railway (same style as job_scaper).
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MALLOC_ARENA_MAX=2
ENV MALLOC_MMAP_THRESHOLD_=65536
ENV MALLOC_TRIM_THRESHOLD_=131072
ENV MALLOC_MMAP_MAX_=65536

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    chromium-driver \
    xauth \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN playwright install --with-deps chromium

COPY . /app

CMD sh -c "xvfb-run -a uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
