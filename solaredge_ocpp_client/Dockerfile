ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir websockets==15.0.1

COPY app /app

CMD ["/opt/venv/bin/python3", "-u", "/app/main.py"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: SolarEdge OCPP Client" \
    org.opencontainers.image.source="https://github.com/nfaa183/ha-solaredge-ocpp-client" \
    org.opencontainers.image.licenses="MIT"
