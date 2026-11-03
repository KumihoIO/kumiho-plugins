# syntax=docker/dockerfile:1
#
# Kumiho hosted MCP resource server. Two stages so the runtime image has no
# compiler and no build cache. ECS runs the image by immutable ECR digest.

FROM python:3.11-slim-trixie@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md requirements.lock ./
COPY kumiho_cloud_mcp ./kumiho_cloud_mcp

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install --require-hashes -r requirements.lock \
 && /opt/venv/bin/pip install --no-deps .


# Keep the exact CPython and locked SDKs, but use supported Ubuntu security
# backports for glibc and zlib. CI tests this actual runtime before publication.
FROM ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    KUMIHO_MCP_HOSTED=1 \
    KUMIHO_STACK_MIDDLE_BAND=0 \
    PORT=8080 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# KUMIHO_STACK_MIDDLE_BAND=0: revision stacking runs strong-only on a shared
# server (score >= 0.75 plus the lexical floor; the 0.55 type-match band is
# withheld). The app sets the same default at startup, so this is belt and
# braces for anything that bypasses create_app -- but the image is also what
# a `docker run ... python -c` debugging session inherits.

# Non-root, and a writable HOME that deliberately contains no ~/.kumiho:
# hosted mode must never pick up ambient credentials.
# No Debian OS libraries are copied into the Ubuntu runtime.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends \
    ca-certificates libssl3t64 libffi8 zlib1g libbz2-1.0 libexpat1 \
    liblzma5 libncursesw6 libreadline8t64 libsqlite3-0 libuuid1 tzdata \
    libgdbm6t64 libgdbm-compat4t64 libtcl8.6 libtk8.6 libcrypt1 \
 && apt-get check \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --create-home --uid 10001 kumiho \
 && find / -xdev -type f -perm /6000 -exec chmod a-s '{}' +

COPY --from=build /usr/local /usr/local
COPY --from=build /opt/venv /opt/venv
COPY scripts/verify_runtime.py /usr/local/bin/verify_runtime.py
RUN ldconfig && python /usr/local/bin/verify_runtime.py

USER kumiho
WORKDIR /home/kumiho
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz', timeout=4).status==200 else 1)"

# configure_logging() takes uvicorn's loggers over at import time, so the
# container emits one JSON object per line for both app and access logs.
# gettext must not consume an inherited LANGUAGE path (CVE-2026-84243).
ENTRYPOINT ["/usr/bin/env", "-u", "LANGUAGE", "LC_ALL=C.UTF-8", "LANG=C.UTF-8"]
CMD ["sh", "-c", "exec uvicorn kumiho_cloud_mcp.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
