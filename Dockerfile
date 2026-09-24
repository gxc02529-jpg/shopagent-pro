FROM python:3.14-slim-bookworm

WORKDIR /app
COPY requirements-runtime.lock ./
RUN pip install --no-cache-dir -r requirements-runtime.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
RUN addgroup --system --gid 10001 shopagent \
    && adduser --system --uid 10001 --ingroup shopagent --home /nonexistent --no-create-home shopagent

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHOPAGENT_HOST=0.0.0.0 \
    SHOPAGENT_PORT=8080
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"
CMD ["shopagent-api"]
