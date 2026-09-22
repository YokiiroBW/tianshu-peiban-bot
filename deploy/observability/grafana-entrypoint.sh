#!/bin/sh
set -eu
# Fixed file references; never enable shell tracing or echo credentials.
OBS_QUERY_TOKEN="$(cat /run/secrets/query_token)"
export OBS_QUERY_TOKEN
exec /run.sh
