FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/knowledge-ingestion-service/pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY services/knowledge-ingestion-service/ .

EXPOSE 3070

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3070"]
