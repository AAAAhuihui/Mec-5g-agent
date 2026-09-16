# Main MEC/5G RAG Agent image. The K8s MCP service has its own Dockerfile at
# mcp_servers/Dockerfile so that it can be deployed with separate permissions.
FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_DEFAULT_TIMEOUT=300

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Keep dependency installation in its own layer so source-only changes reuse it.
COPY requirements.txt ./requirements.txt
RUN python -m pip install \
    --no-cache-dir \
    --timeout "${PIP_DEFAULT_TIMEOUT}" \
    --retries 10 \
    --index-url "${PIP_INDEX_URL}" \
    -r requirements.txt

# The API does not need root privileges. Runtime credentials must be supplied
# through Kubernetes Secrets or docker --env-file, never baked into the image.
RUN useradd --create-home --uid 10001 agentuser
COPY --chown=agentuser:agentuser app ./app
COPY --chown=agentuser:agentuser data/raw_docs ./data/raw_docs

USER agentuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
