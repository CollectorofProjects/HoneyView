#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

INSTALL_DIR="/opt/honeyview"
REPO_URL="${HONEYVIEW_REPO_URL:-https://github.com/CollectorofProjects/HoneyView.git}"
REPO_REF="${HONEYVIEW_REPO_REF:-main}"
PUBLIC_SSH_PORT="${HONEYVIEW_PUBLIC_SSH_PORT:-2222}"
DASHBOARD_USER="${HONEYVIEW_DASHBOARD_USER:-analyst}"
ACCEPT_RISK=0

log() { printf '\n[HoneyView] %s\n' "$*"; }
fail() { printf '\n[HoneyView] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
HoneyView Ubuntu installer

Usage:
  sudo bash honeyview.sh [options] --accept-risk

Options:
  --repo URL             Repository override (default: CollectorofProjects/HoneyView)
  --ref REF              Branch or release tag (default: main)
  --ssh-port PORT        Public decoy SSH port (default: 2222)
  --dashboard-user USER  Dashboard Basic Auth username (default: analyst)
  --accept-risk          Confirm authorized use and acceptance of DISCLAIMER.md
  --help                 Show this help

The installer supports clean 64-bit Ubuntu 22.04, 24.04, and 26.04 LTS hosts.
It installs into /opt/honeyview and keeps the dashboard on 127.0.0.1:8080.
EOF
}

while (($#)); do
  case "$1" in
    --repo) [[ $# -ge 2 ]] || fail "--repo requires a value"; REPO_URL="$2"; shift 2 ;;
    --ref) [[ $# -ge 2 ]] || fail "--ref requires a value"; REPO_REF="$2"; shift 2 ;;
    --ssh-port) [[ $# -ge 2 ]] || fail "--ssh-port requires a value"; PUBLIC_SSH_PORT="$2"; shift 2 ;;
    --dashboard-user) [[ $# -ge 2 ]] || fail "--dashboard-user requires a value"; DASHBOARD_USER="$2"; shift 2 ;;
    --accept-risk) ACCEPT_RISK=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || fail "run this installer as root, normally with sudo"
[[ $ACCEPT_RISK -eq 1 ]] || fail "read DISCLAIMER.md and rerun with --accept-risk"
[[ "$REPO_URL" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] || \
  fail "--repo must be an HTTPS github.com repository URL"
[[ "$REPO_REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || fail "invalid --ref value"
[[ "$REPO_REF" != *".."* ]] || fail "invalid --ref value"
[[ "$DASHBOARD_USER" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || fail "invalid dashboard username"
[[ "$PUBLIC_SSH_PORT" =~ ^[0-9]+$ ]] || fail "SSH port must be numeric"
((PUBLIC_SSH_PORT >= 1 && PUBLIC_SSH_PORT <= 65535)) || fail "SSH port must be between 1 and 65535"

[[ -r /etc/os-release ]] || fail "cannot identify the operating system"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "this installer supports Ubuntu only"
case "${VERSION_CODENAME:-}" in
  jammy|noble|resolute) ;;
  *) fail "supported Ubuntu LTS releases are 22.04, 24.04, and 26.04" ;;
esac

[[ "$(dpkg --print-architecture)" =~ ^(amd64|arm64)$ ]] || \
  fail "this automated installer currently supports amd64 and arm64"

if [[ -e "$INSTALL_DIR" ]]; then
  fail "$INSTALL_DIR already exists; preserve it and perform a reviewed update instead"
fi

if [[ "$PUBLIC_SSH_PORT" == "22" ]] && ss -ltn 2>/dev/null | grep -Eq '[:.]22[[:space:]]'; then
  fail "TCP/22 is already in use. Keep HoneyView on 2222 or move management SSH first."
fi

export DEBIAN_FRONTEND=noninteractive

log "Installing host prerequisites"
apt-get update
apt-get install -y ca-certificates curl git openssl iproute2 iptables unattended-upgrades

if command -v docker >/dev/null 2>&1; then
  docker compose version >/dev/null 2>&1 || \
    fail "Docker is present but the Compose plugin is unavailable; repair that installation before continuing"
else
  log "Installing Docker Engine and Compose from Docker's signed apt repository"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  architecture="$(dpkg --print-architecture)"
  printf '%s\n' \
    'Types: deb' \
    'URIs: https://download.docker.com/linux/ubuntu' \
    "Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}" \
    'Components: stable' \
    "Architectures: $architecture" \
    'Signed-By: /etc/apt/keyrings/docker.asc' \
    > /etc/apt/sources.list.d/docker.sources
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

systemctl enable --now docker
systemctl enable --now unattended-upgrades.service >/dev/null 2>&1 || true

log "Downloading HoneyView $REPO_REF"
git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$INSTALL_DIR"
[[ -f "$INSTALL_DIR/compose.yaml" && -f "$INSTALL_DIR/Dockerfile" ]] || \
  fail "the selected repository/ref is not a HoneyView release"
[[ -x "$INSTALL_DIR/deploy/harden-firewall.sh" && -x "$INSTALL_DIR/deploy/verify-isolation.sh" ]] || \
  fail "deployment scripts are missing or not executable"

dashboard_password="$(openssl rand -base64 36 | tr -d '\n')"
fingerprint_key="$(openssl rand -hex 32)"
cat > "$INSTALL_DIR/.env" <<EOF
HONEYVIEW_DASHBOARD_USER=$DASHBOARD_USER
HONEYVIEW_DASHBOARD_PASSWORD=$dashboard_password
HONEYVIEW_FINGERPRINT_KEY=$fingerprint_key
HONEYVIEW_WEB_HOST=0.0.0.0
HONEYVIEW_WEB_PORT=8080
HONEYVIEW_SSH_HOST=0.0.0.0
HONEYVIEW_SSH_PORT=2222
HONEYVIEW_PUBLIC_SSH_PORT=$PUBLIC_SSH_PORT
HONEYVIEW_RETENTION_DAYS=30
HONEYVIEW_SESSION_IDLE_SECONDS=180
HONEYVIEW_SESSION_MAX_SECONDS=1800
HONEYVIEW_MAX_COMMAND_LENGTH=4096
HONEYVIEW_MAX_CONCURRENT_SESSIONS=64
HONEYVIEW_SSH_SERVER_VERSION=OpenSSH_8.9p1 Ubuntu-3ubuntu0.10
EOF
chmod 0600 "$INSTALL_DIR/.env"
chown -R root:root "$INSTALL_DIR"

log "Building the HoneyView application image"
cd "$INSTALL_DIR"
docker compose config --quiet
docker compose build --pull
docker compose create

log "Installing the sensor isolation firewall service"
install -m 0644 "$INSTALL_DIR/deploy/honeyview.service" /etc/systemd/system/honeyview.service
cat > /etc/systemd/system/honeyview-firewall.service <<'EOF'
[Unit]
Description=HoneyView sensor isolation firewall
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
Before=honeyview.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/honeyview/deploy/harden-firewall.sh

[Install]
WantedBy=multi-user.target
EOF
install -d -m 0755 /etc/systemd/system/honeyview.service.d
cat > /etc/systemd/system/honeyview.service.d/firewall.conf <<'EOF'
[Unit]
Requires=honeyview-firewall.service
After=honeyview-firewall.service
EOF
systemctl daemon-reload
systemctl enable --now honeyview-firewall.service
systemctl enable --now honeyview.service

log "Waiting for HoneyView to become healthy"
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/healthz >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
if [[ $healthy -ne 1 ]]; then
  docker compose logs --tail=100 honeyview >&2 || true
  docker compose down
  fail "health check failed; HoneyView was stopped"
fi

log "Verifying isolation controls"
if ! "$INSTALL_DIR/deploy/verify-isolation.sh"; then
  docker compose down
  fail "isolation verification failed; HoneyView was stopped"
fi

installed_commit="$(git -C "$INSTALL_DIR" rev-parse HEAD)"

cat <<EOF

HoneyView installation completed.

  Installed commit: $installed_commit
  Decoy SSH port:   $PUBLIC_SSH_PORT/tcp
  Dashboard:        http://127.0.0.1:8080 (loopback only)
  Dashboard user:   $DASHBOARD_USER
  Dashboard pass:   $dashboard_password

Save the dashboard password now. It is stored root-only in:
  $INSTALL_DIR/.env

Access the dashboard through an SSH tunnel from your workstation:
  ssh -L 8080:127.0.0.1:8080 ADMIN_USER@SERVER_IP

Then open http://127.0.0.1:8080 locally.

REQUIRED BEFORE PUBLIC EXPOSURE:
  1. Allow TCP/$PUBLIC_SSH_PORT inbound in the provider firewall.
  2. Keep TCP/8080 blocked publicly.
  3. Deny new outbound traffic at the provider firewall while allowing replies.
  4. Restrict the real management SSH port to your IP or VPN.
  5. Confirm the VM has no production routes, cloud role, or sensitive data.

Read $INSTALL_DIR/RUNBOOK.md before opening the decoy port.
EOF
