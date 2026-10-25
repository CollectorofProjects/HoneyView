FROM python:3.14.0-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HONEYVIEW_DATA_DIR=/data

RUN groupadd --gid 10001 honeyview \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin honeyview \
    && mkdir -p /app /data \
    && chown -R honeyview:honeyview /app /data

WORKDIR /app
COPY --chown=honeyview:honeyview requirements.lock ./
RUN pip install --require-hashes --no-compile --requirement requirements.lock
COPY --chown=honeyview:honeyview app ./app

USER 10001:10001
EXPOSE 2222 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

CMD ["python", "-m", "app.main"]
