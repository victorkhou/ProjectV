#####
# Docker image for the RTS Combat Overworld game (Evennia 6.0, Python 3.14).
#
# This repo is a monorepo: it vendors the Evennia framework at ./evennia and the
# game instance at ./mygame. This image installs the vendored Evennia in
# editable mode (so `import evennia` resolves to the in-repo copy) and runs the
# game out of ./mygame.
#
# Quick start (recommended - uses docker-compose.yml):
#     docker compose up --build
#   then connect a MUD client to localhost:4000 or open http://localhost:4001
#
# Build/run directly (without compose):
#     docker build -t projectv .
#     docker run -it --rm -p 4000:4000 -p 4001:4001 -p 4002:4002 \
#         -v "$PWD:/usr/src/projectv" projectv
#
# The game database (SQLite), secret_settings.py and logs are written under
# mygame/server/ - bind-mounting the repo (as compose does) keeps them on the
# host so they survive container recreation.
#####
FROM python:3.14-slim

LABEL maintainer="ProjectV"

# Keep Python output unbuffered so logs stream to `docker logs`, and don't write
# .pyc files into the mounted source tree.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Point the interpreter at the vendored Evennia (matches how the live server
# treats mygame/ as the Python root; see the repo README's import-paths note).
ENV PYTHONPATH=/usr/src/projectv

# Build toolchain + runtime libs. Most core deps ship manylinux wheels for
# 3.14, but keep a small toolchain so any source build (e.g. cffi) still works.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/projectv

# Install dependencies first for better layer caching: copy only the packaging
# metadata + the vendored evennia package, install, then add the rest.
COPY pyproject.toml setup.py ./
COPY evennia ./evennia
RUN pip install --upgrade pip && pip install -e .

# Add the remaining source (game code, etc.). At runtime compose bind-mounts the
# repo over this, so edits on the host are picked up live.
COPY . .

# Telnet, webserver (web client + website), and websocket client ports.
EXPOSE 4000 4001 4002

# Invoke the entrypoint via bash so it works even when the host copy lacks the
# executable bit (common when building/mounting from Windows).
ENTRYPOINT ["bash", "/usr/src/projectv/docker-entrypoint.sh"]
# Default command: run the server in the foreground with logs to the console.
CMD ["evennia", "start", "-l"]
