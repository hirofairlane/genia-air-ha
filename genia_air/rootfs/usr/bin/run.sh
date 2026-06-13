#!/usr/bin/env sh
# Entrypoint — Python does the supervisor introspection itself; we just exec.
mkdir -p /data/logs
exec python3 /usr/bin/genia_air.py
