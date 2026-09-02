# syntax=docker/dockerfile:1
#
# Kumiho hosted MCP resource server. Two stages so the runtime image has no
# compiler and no build cache: App Runner pulls this on every deploy.

FROM python:3.11-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md ./
COPY kumiho_cloud_mcp ./kumiho_cloud_mcp

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .


FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    KUMIHO_MCP_HOSTED=1 \
    KUMIHO_STACK_MIDDLE_BAND=0 \
    PORT=8080

# KUMIHO_STACK_MIDDLE_BAND=0: revision stacking runs strong-only on a shared
# server (score >= 0.75 plus the lexical floor; the 0.55 type-match band is
# withheld). The app sets the same default at startup, so this is belt and
# braces for anything that bypasses create_app -- but the image is also what
# a `docker run ... python -c` debugging session inherits.

# Non-root, and a writable HOME that deliberately contains no ~/.kumiho:
# hosted mode must never pick up ambient credentials.
RUN useradd --system --create-home --uid 10001 kumiho

COPY --from=build /opt/venv /opt/venv

USER kumiho
WORKDIR /home/kumiho
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz', timeout=4).status==200 else 1)"

# configure_logging() takes uvicorn's loggers over at import time, so the
# container emits one JSON object per line for both app and access logs.
CMD ["sh", "-c", "exec uvicorn kumiho_cloud_mcp.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
