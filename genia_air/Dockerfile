ARG BUILD_FROM
FROM $BUILD_FROM

ARG BUILD_ARCH
ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1

# Install runtime deps + ebusd (john30) from the upstream release .deb.
# Debian 12 (bookworm) is the HA base; we pick the matching .deb arch.
ARG EBUSD_VERSION=25.1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-flask python3-paho-mqtt \
        python3-requests python3-tz python3-apscheduler \
        wget ca-certificates jq tini; \
    case "${BUILD_ARCH}" in \
        amd64)    EBUSD_ARCH=amd64 ;; \
        aarch64)  EBUSD_ARCH=arm64 ;; \
        armv7)    EBUSD_ARCH=armhf ;; \
        armhf)    EBUSD_ARCH=armhf ;; \
        i386)     EBUSD_ARCH=i386 ;; \
        *) echo "Unsupported BUILD_ARCH=${BUILD_ARCH}"; exit 1 ;; \
    esac; \
    DEB_FILE="ebusd-${EBUSD_VERSION}_${EBUSD_ARCH}-debian12_mqtt1.deb"; \
    wget -q -O "/tmp/${DEB_FILE}" \
        "https://github.com/john30/ebusd/releases/download/v${EBUSD_VERSION}/${DEB_FILE}"; \
    apt-get install -y "/tmp/${DEB_FILE}"; \
    rm -rf /tmp/${DEB_FILE} /var/lib/apt/lists/*

COPY rootfs /
RUN chmod +x /usr/bin/run.sh /usr/bin/genia_air.py

# tini = PID 1 (proper signal handling for the python parent which spawns ebusd)
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/run.sh"]
