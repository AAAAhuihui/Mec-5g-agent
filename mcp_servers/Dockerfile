FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_DEFAULT_TIMEOUT=300

WORKDIR /app
COPY mcp_servers/requirements.txt /app/requirements.txt
RUN python -m pip install \
    --no-cache-dir \
    --timeout "${PIP_DEFAULT_TIMEOUT}" \
    --retries 10 \
    --index-url "${PIP_INDEX_URL}" \
    -r /app/requirements.txt
RUN useradd --create-home --uid 10001 mcpuser
COPY --chown=mcpuser:mcpuser mcp_servers /app/mcp_servers

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
USER mcpuser
CMD ["python", "-m", "mcp_servers.k8s_server"]
