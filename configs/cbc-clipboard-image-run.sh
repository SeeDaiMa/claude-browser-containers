#!/bin/sh
set -u

export DISPLAY="${DISPLAY:-:0}"
while true; do
    /usr/local/bin/cbc-clipboard-image
    status=$?
    if [ "$status" -eq 0 ]; then
        exit 0
    fi
    sleep 1
done
