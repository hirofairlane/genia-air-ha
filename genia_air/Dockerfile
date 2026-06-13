ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1

RUN apk add --no-cache python3 py3-pip jq \
 && pip install --no-cache-dir --break-system-packages \
        flask==3.0.0 \
        paho-mqtt==1.6.1 \
        APScheduler==3.10.4 \
        requests==2.31.0

COPY rootfs /
RUN chmod +x /usr/bin/run.sh /usr/bin/genia_air.py

CMD ["/usr/bin/run.sh"]
