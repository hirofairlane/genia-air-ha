#!/usr/bin/with-contenv sh
# Entrypoint — `with-contenv` injects the supervisor-provided env vars
# (SUPERVISOR_TOKEN, HASSIO_TOKEN, ...) that the s6 init otherwise scrubs
# for legacy-services launches.
mkdir -p /data/logs
exec python3 /usr/bin/genia_air.py
