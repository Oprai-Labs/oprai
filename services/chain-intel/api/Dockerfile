# chain-intel API — read-only analysis-object service over the ClickHouse index.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115.0" "uvicorn[standard]>=0.32.0" "httpx>=0.28.0"
# package is imported as `api` (endpoints reference `.ch` / `.reports`)
COPY . /app/api
ENV CH_URL=http://rh-clickhouse:8123/ CH_USER=oprai CH_DB=rh
EXPOSE 3160
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "3160"]
