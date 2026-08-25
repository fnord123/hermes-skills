# webaccess — one handler core, two facades (HTTP 8910, MCP 8911).
#
# Built ON the docker host (.226): the Fara scaffold, Playwright/Chromium and
# xvfb are baked in, the Fara local patches are applied here (they are
# load-bearing for fetch's agent rung), and the service state lives in
# WEBACCESS_HOME (a named volume). Nothing in this image is a secret: the
# only runtime credential (the LiteLLM key) comes in via the process env.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    WEBACCESS_HOME=/var/lib/webaccess \
    FARA_HOME=/opt/fara \
    HOME=/root

# Browser rungs need a virtual display, real fonts, and Chromium's shared
# libraries. xvfb is headful mode; fonts matter because Fara reads pages
# from screenshots — missing glyphs become model noise.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        fontconfig \
        fonts-liberation \
        ca-certificates \
        procps \
        patch \
    && rm -rf /var/lib/apt/lists/*

# Fetch tier: PDF text extraction.
COPY scripts/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The Fara scaffold: clean source + the local patches, in its own venv, the
# same layout the dev machine uses (so FARA_HOME/.venv/bin/fara-cli resolves
# identically). The patch is part of the build, not a hand step: the README
# says it moves into the image build, and a re-clone must not be able to
# lose it.
COPY patches/fara-local-patches.diff /tmp/fara-local-patches.diff
COPY fara /opt/fara
RUN set -eux; \
    python3 -m venv /opt/fara/.venv; \
    /opt/fara/.venv/bin/pip install --no-cache-dir -e /opt/fara; \
    patch -p1 -d /opt/fara --dry-run < /tmp/fara-local-patches.diff; \
    patch -p1 -d /opt/fara < /tmp/fara-local-patches.diff; \
    grep -q "FARA_INIT_COOKIES" /opt/fara/src/fara/environments/playwright/environment.py; \
    grep -q "FARA_DUMP_MARKDOWN" /opt/fara/src/fara/run_fara.py; \
    grep -q "advanced_stealth" /opt/fara/src/fara/environments/playwright/environment.py; \
    /opt/fara/.venv/bin/playwright install --with-deps chromium; \
    rm /tmp/fara-local-patches.diff

# The handler core and its facades.
COPY scripts/app.py /opt/webaccess/app.py
COPY scripts/mcp_server.py /opt/webaccess/mcp_server.py
COPY scripts/service.py /opt/webaccess/service.py
COPY scripts/handlers.py /opt/webaccess/handlers.py
COPY scripts/rxfetch.py /opt/webaccess/rxfetch.py
COPY scripts/browse_task.py /opt/webaccess/browse_task.py
COPY scripts/run_service.py /opt/webaccess/run_service.py
COPY service.yaml /opt/webaccess/service.yaml
# Bladebro tier needs the docker CLI. Official static build: the host's
# /usr/bin/docker is musl-linked and will not run in this Debian image.
COPY docker-client /usr/local/bin/docker
RUN chmod +x /usr/local/bin/docker && docker --version

EXPOSE 8910 8911
CMD ["python3", "/opt/webaccess/run_service.py"]
