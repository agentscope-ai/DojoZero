#!/bin/sh
set -eu

echo "Starting all-in-one services (jaeger + serve + arena)"
exec supervisord -n -c /app/docker/allinone/supervisord.full.conf
