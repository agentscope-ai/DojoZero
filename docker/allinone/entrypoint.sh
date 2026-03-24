#!/bin/sh
set -eu

require_api_keys() {
  if [ -z "${DOJOZERO_DASHSCOPE_API_KEY:-}" ] || [ -z "${DOJOZERO_TAVILY_API_KEY:-}" ]; then
    echo "Missing required env vars: DOJOZERO_DASHSCOPE_API_KEY and DOJOZERO_TAVILY_API_KEY"
    exit 1
  fi
}

require_api_keys
echo "Starting all-in-one services (jaeger + serve + arena)"
exec supervisord -n -c /app/docker/allinone/supervisord.full.conf
