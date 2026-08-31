# trixie (Debian 13, glibc 2.41): the prebuilt bladebro binary needs GLIBC_2.39, so
# bookworm's 2.36 is too old and the binary refuses to load.
FROM node:22-trixie-slim

# Chromium (bladebro drives it over CDP) + Xvfb (headful virtual display — bladebro's
# stealth is weaker in true headless) + fonts + dumb-init for clean PID1 signal handling.
# The chromium package pulls its own shared-lib deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium xvfb dumb-init ca-certificates curl \
      fonts-liberation fonts-noto-color-emoji fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# bladebro = the agentic browser driver (prebuilt per-platform binary, no build).
# supergateway bridges its stdio MCP to Streamable-HTTP so Hermes can reach it by URL.
RUN npm install -g bladebro supergateway \
    && npm cache clean --force

# Run unprivileged. Chromium's user-namespace sandbox works non-root under Docker's
# default seccomp, so no SYS_ADMIN / --no-sandbox is needed on a modern kernel.
RUN useradd -m -u 10001 -s /usr/sbin/nologin blade
USER blade
WORKDIR /home/blade

ENV CHROME_PATH=/usr/bin/chromium \
    BLADE_PROFILE_DIR=/home/blade/profile \
    DISPLAY=:99 \
    PORT=8000

COPY --chown=blade:blade entrypoint.sh /home/blade/entrypoint.sh
COPY --chown=blade:blade fetch.js /home/blade/fetch.js
EXPOSE 8000
ENTRYPOINT ["/usr/bin/dumb-init","--","/bin/bash","/home/blade/entrypoint.sh"]
