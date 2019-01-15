#!/bin/sh

set -e
cd /ecobee/config
while true; do
    python /ecobee/ecobee.py
    echo "Waiting 10 min..."
    for ab in $(seq 9 -1 0); do
        sleep 60
        if [ $ab -gt 0 ]; then
            echo "$ab..."
        else
            echo "again!"
        fi
    done

done