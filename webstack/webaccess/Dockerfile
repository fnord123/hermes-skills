# webaccess — one handler core, two facades (HTTP 8910, MCP 8911).
#
# Fully re-derivable build: the two large assets (the Fara scaffold and the
# docker CLI) are FETCHED at build time at pinned, hash-verified upstream
# coordinates. Nothing multi-megabyte is committed to git.
#
#   Fara:    microsoft/fara @ a675d6d (verified byte-identical to the
#            deployed .226 tree, 174/174 files, 2026-08-31)
#   docker:  official static 27.3.1 (sha256-pinned; byte-identical to the
#            binary that ran on .226)
#
# Service code (scripts/, service.yaml, assets/) is synced into this build
# context from the skill at repo root (web-access/) by sync-from-skill.sh —
# the skill stays the single source of truth for the handler code.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    WEBACCESS_HOME=/var/lib/webaccess \
    FARA_HOME=/opt/fara \
    HOME=/root

# Browser rungs need a virtual display, real fonts, and Chromium's shared
# libraries. xvfb is headful mode; fonts matter because Fara reads pages
# from screenshots — missing glyphs become model noise. curl is needed for
# the pinned asset fetches below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        fontconfig \
        fonts-liberation \
        ca-certificates \
        procps \
        patch \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Fetch tier: PDF text extraction.
COPY scripts/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# --- Fara: pinned upstream commit, verified against the 174-file manifest ---
# The local patches (FARA_INIT_COOKIES, FARA_DUMP_MARKDOWN, advanced_stealth,
# --no-sandbox) are load-bearing for the agent rung and are applied here —
# the patch is part of the build, not a hand step, so a re-clone cannot lose it.
ARG FARA_REF=a675d6d61c41c47ae87bacefeab22caad18e3e84
COPY assets/fara-local-patches.diff /tmp/fara-local-patches.diff
COPY fara-manifest.sha256 /tmp/fara-manifest.sha256
RUN set -eux; \
    curl -fsSL "https://codeload.github.com/microsoft/fara/tar.gz/${FARA_REF}" -o /tmp/fara.tgz; \
    mkdir -p /opt/fara; \
    tar -xzf /tmp/fara.tgz -C /opt/fara --strip-components=1; \
    cd /opt/fara && sha256sum -c --quiet /tmp/fara-manifest.sha256; \
    python3 -m venv /opt/fara/.venv; \
    /opt/fara/.venv/bin/pip install --no-cache-dir -e /opt/fara; \
    patch -p1 -d /opt/fara --dry-run < /tmp/fara-local-patches.diff; \
    patch -p1 -d /opt/fara < /tmp/fara-local-patches.diff; \
    grep -q "FARA_INIT_COOKIES" /opt/fara/src/fara/environments/playwright/environment.py; \
    grep -q "FARA_DUMP_MARKDOWN" /opt/fara/src/fara/run_fara.py; \
    grep -q "advanced_stealth" /opt/fara/src/fara/environments/playwright/environment.py; \
    /opt/fara/.venv/bin/playwright install --with-deps chromium; \
    rm /tmp/fara.tgz /tmp/fara-manifest.sha256 /tmp/fara-local-patches.diff

# --- docker CLI: official static build, pinned + hash-verified ---
# The host's /usr/bin/docker is musl-linked and will not run in this Debian
# image; the bladebro tier needs a glibc static docker inside the container.
ARG DOCKER_VERSION=27.3.1
ARG DOCKER_TGZ_SHA256=9b4f6fe406e50f9085ee474c451e2bb5adb119a03591f467922d3b4e2ddf31d3
ARG DOCKER_BIN_SHA256=fce9b87ce16bc531383c9163eac43c78699cedc6ef332d78a67ca3673476b518
RUN set -eux; \
    curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" -o /tmp/docker.tgz; \
    echo "${DOCKER_TGZ_SHA256}  /tmp/docker.tgz" | sha256sum -c -; \
    tar -xzf /tmp/docker.tgz -C /tmp; \
    echo "${DOCKER_BIN_SHA256}  /tmp/docker/docker" | sha256sum -c -; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    docker --version; \
    rm -rf /tmp/docker /tmp/docker.tgz

# The handler core and its facades (synced from the skill — see README).
COPY scripts/app.py /opt/webaccess/app.py
COPY scripts/mcp_server.py /opt/webaccess/mcp_server.py
COPY scripts/service.py /opt/webaccess/service.py
COPY scripts/handlers.py /opt/webaccess/handlers.py
COPY scripts/rxfetch.py /opt/webaccess/rxfetch.py
COPY scripts/browse_task.py /opt/webaccess/browse_task.py
COPY scripts/run_service.py /opt/webaccess/run_service.py
COPY service.yaml /opt/webaccess/service.yaml

EXPOSE 8910 8911
CMD ["python3", "/opt/webaccess/run_service.py"]
