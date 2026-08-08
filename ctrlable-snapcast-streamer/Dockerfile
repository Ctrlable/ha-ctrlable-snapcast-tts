ARG BUILD_FROM
FROM ${BUILD_FROM}

# The add-on builder passes the version from config.yaml as BUILD_VERSION.
# Taking it from there makes config.yaml the ONE place a version is written --
# previously main.py and the web UI each carried their own copy and both drifted
# (config 0.1.26, /status 0.1.24, UI badge 0.1.0).
ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION}

RUN apk add --no-cache \
    ffmpeg \
    openssh-client \
    && pip install --no-cache-dir \
    fastapi==0.115.0 \
    uvicorn==0.30.6 \
    httpx==0.27.2 \
    structlog==24.4.0 \
    jinja2==3.1.4 \
    python-multipart==0.0.9

COPY app/ /app/
# config.yaml goes INTO the image on purpose: Supervisor does not reliably pass
# BUILD_VERSION for locally-built add-ons (observed 2026-08-08 -- ADDON_VERSION
# came through empty and the UI fell back to 0.0.0-dev), so the app reads the
# version straight out of it. Belt and braces, no build args required.
COPY config.yaml /app/config.yaml
COPY rootfs/ /

WORKDIR /app
