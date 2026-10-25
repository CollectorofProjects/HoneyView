#!/bin/sh
set -eu

# Run only on the dedicated decoy VM after reviewing the variables below.
# Docker routes published-port traffic through DOCKER-USER. These rules allow
# replies to inbound connections but block new connections from the sensor to
# the Internet, other routed networks, and services on the Docker host.

SENSOR_IP="${HONEYVIEW_SENSOR_IP:-172.30.77.10}"
SENSOR_SUBNET="${HONEYVIEW_SENSOR_SUBNET:-172.30.77.0/24}"

if ! command -v iptables >/dev/null 2>&1; then
  echo "iptables is required; use equivalent nftables rules on this host." >&2
  exit 1
fi

iptables -N DOCKER-USER 2>/dev/null || true
iptables -C DOCKER-USER -s "$SENSOR_IP" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
  iptables -I DOCKER-USER 1 -s "$SENSOR_IP" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -C DOCKER-USER -s "$SENSOR_IP" -j DROP 2>/dev/null || \
  iptables -A DOCKER-USER -s "$SENSOR_IP" -j DROP
iptables -C INPUT -s "$SENSOR_SUBNET" -j DROP 2>/dev/null || \
  iptables -I INPUT 1 -s "$SENSOR_SUBNET" -j DROP

echo "HoneyView sensor egress and host access are blocked. Persist these rules using your distribution's firewall tooling."
