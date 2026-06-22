ARG BUILD_FROM
FROM $BUILD_FROM

ARG BUILD_ARCH
ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1

# Install runtime deps + ebusd (john30) from the upstream release .deb.
# Asset name format (release v26.1): ebusd-26.1_<arch>-bookworm_mqtt1.deb
ARG EBUSD_VERSION=26.1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-flask python3-paho-mqtt \
        python3-requests python3-tz python3-apscheduler \
        wget ca-certificates jq; \
    case "${BUILD_ARCH}" in \
        amd64)    EBUSD_ARCH=amd64 ;; \
        aarch64)  EBUSD_ARCH=arm64 ;; \
        *) echo "Unsupported BUILD_ARCH=${BUILD_ARCH}"; exit 1 ;; \
    esac; \
    DEB_FILE="ebusd-${EBUSD_VERSION}_${EBUSD_ARCH}-bookworm_mqtt1.deb"; \
    wget -q -O "/tmp/${DEB_FILE}" \
        "https://github.com/john30/ebusd/releases/download/${EBUSD_VERSION}/${DEB_FILE}"; \
    apt-get install -y "/tmp/${DEB_FILE}"; \
    rm -rf /tmp/${DEB_FILE} /var/lib/apt/lists/*

COPY rootfs /
RUN chmod +x /usr/bin/run.sh /usr/bin/genia_air.py

# Inherit the HA base image's s6-overlay init (/init); our run.sh becomes the
# main process via CMD, with s6 wrapping it. Python catches SIGTERM and
# tears down the ebusd subprocess cleanly.
CMD ["/usr/bin/run.sh"]
