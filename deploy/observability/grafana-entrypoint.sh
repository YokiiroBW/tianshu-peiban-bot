#!/bin/sh
set -eu
# Deployment file reference, with the standalone default; never trace or echo credentials.
OBS_QUERY_TOKEN="$(cat "${QUERY_TOKEN_FILE:-/run/secrets/query_token}")"
export OBS_QUERY_TOKEN
exec /run.sh
