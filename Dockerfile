FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/mobile-shop
COPY requirements-worker.txt ./
RUN python -m pip install -r requirements-worker.txt
COPY app ./app
COPY scripts ./scripts

CMD ["celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=INFO"]
