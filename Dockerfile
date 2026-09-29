FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/between.db

RUN groupadd --system --gid 1000 between \
 && useradd --system --uid 1000 --gid between --home-dir /app --shell /usr/sbin/nologin between \
 && mkdir -p /app /data \
 && chown between:between /app /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=between:between app ./app

USER between
EXPOSE 8000
# --proxy-headers only trusts FORWARDED_ALLOW_IPS (set in the GCP compose file, where Caddy is the only peer).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header", "--no-date-header"]
