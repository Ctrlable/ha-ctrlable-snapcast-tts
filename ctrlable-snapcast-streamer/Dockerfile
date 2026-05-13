ARG BUILD_FROM
FROM ${BUILD_FROM}

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
COPY rootfs/ /

WORKDIR /app
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8099"]
