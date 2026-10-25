#!/bin/sh
set -eu

echo "Checking dashboard is loopback-bound..."
docker compose port honeyview 8080 | grep -Eq '127\.0\.0\.1:8080|\[::1\]:8080'

echo "Checking the container is non-root and capability-free..."
test "$(docker compose exec -T honeyview id -u)" = "10001"
docker inspect "$(docker compose ps -q honeyview)" --format '{{json .HostConfig.CapDrop}}' | grep -q 'ALL'
docker inspect "$(docker compose ps -q honeyview)" --format '{{.HostConfig.ReadonlyRootfs}}' | grep -q 'true'

echo "Checking outbound access is blocked (failure is expected)..."
if docker compose exec -T honeyview python -c "import socket; socket.create_connection(('1.1.1.1', 443), 3)"; then
  echo "ERROR: the sensor still has outbound network access" >&2
  exit 1
fi

echo "Checking service health..."
curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null
echo "Isolation checks passed."
