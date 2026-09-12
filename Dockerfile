FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    # Admin authentication: REQUIRED at runtime, no default on purpose.
    # docker run -e ADMIN_TOKEN=<secret> ...
    ADMIN_TOKEN="" \
    # Optional: key for signing the admin session cookie (random per start if empty)
    SESSION_SECRET="" \
    SESSION_TTL_HOURS=12 \
    DISCOVERY_TIMEOUT_SECONDS=5 \
    # Listen address/port inside the container (map the port on the host as you like)
    HOST=0.0.0.0 \
    PORT=8000 \
    # HTTPS: enabled by default with a self-signed certificate generated on first
    # start into $DATA_DIR/certs. Set TLS_ENABLED=false for plain HTTP.
    TLS_ENABLED=true \
    TLS_CERT_FILE="" \
    TLS_KEY_FILE="" \
    TLS_HOSTNAMES="" \
    TLS_CERT_DAYS=3650 \
    TLS_REGENERATE=false

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 hue \
    && mkdir -p /data && chown -R hue:hue /data /app
USER hue

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import os,ssl,sys,urllib.request; tls=os.environ.get('TLS_ENABLED','true').lower() in ('1','true','yes','on'); ctx=ssl._create_unverified_context() if tls else None; url=('https' if tls else 'http')+'://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/v1/health'; sys.exit(0 if urllib.request.urlopen(url, timeout=3, context=ctx).status == 200 else 1)"

CMD ["python", "-m", "app.serve"]
