#!/usr/bin/env bash
# Deploy DojoZero containers.
# Reads .env from the project root for variable substitution.
#
# Usage:
#   deploy/up.sh                    # uses DOJOZERO_ENV from .env (default: daily)
#   DOJOZERO_ENV=pre deploy/up.sh   # override tier
#   deploy/up.sh --build             # rebuild images

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Source .env for docker-compose variable substitution
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

exec docker-compose -f "$SCRIPT_DIR/docker-compose.yml" up -d "$@"
