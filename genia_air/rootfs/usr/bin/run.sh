#!/usr/bin/with-contenv sh
# Entrypoint — `with-contenv` injects the supervisor-provided env vars
# (SUPERVISOR_TOKEN, HASSIO_TOKEN, etc.) that s6 otherwise scrubs.
mkdir -p /data/logs
exec python3 /usr/bin/genia_air.py
