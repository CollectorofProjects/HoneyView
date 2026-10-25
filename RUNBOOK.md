# HoneyView deployment and operations runbook

> By deploying HoneyView, the operator acknowledges [DISCLAIMER.md](DISCLAIMER.md), accepts responsibility for lawful authorization and operation, and understands that no technical control or disclaimer guarantees immunity from compromise, loss, claims, or legal action.

## 1. Safety model

Treat the honeypot as hostile infrastructure from the moment it is reachable. Even though HoneyView does not execute attacker commands, the SSH parser, web framework, container runtime and host remain attack surface.

Mandatory boundaries:

1. Use a dedicated disposable VM, cloud account/project, or isolated VLAN. Do not colocate it with production workloads.
2. Give the VM no route to production, management, CI/CD, databases, metadata services, shared storage or corporate identity systems.
3. Deny outbound Internet traffic by default. Use both the cloud firewall and the supplied host-firewall rules; the Compose bridge uses a fixed sensor IP so published inbound ports continue to work while new outbound connections are dropped.
4. Expose only the decoy SSH port. Keep the dashboard private behind a VPN, SSH tunnel, identity-aware proxy or management allowlist.
5. Never place genuine usernames, passwords, private keys, tokens, customer data, binaries or production configuration in the decoy.
6. Obtain legal/privacy approval for collection in your jurisdiction. IP addresses, usernames and attacker input may be personal data.

## 2. Recommended topology

```text
Untrusted Internet
       │
       │ TCP/22 or TCP/2222 only
       ▼
Dedicated decoy VM ──X── production networks
       │
       ├── container: read-only root, no Linux capabilities
       ├── /data: dedicated encrypted volume
       └── dashboard: loopback only
                    ▲
                    │ VPN or SSH local-forward
               analyst device
```

Use a separate administrative SSH port restricted to your fixed IP or VPN. Never replace the host's management SSH service with HoneyView until you have verified an independent recovery path.

### VPS purchasing guide

HoneyView is intentionally small, but the VPS must also accommodate Linux, Docker, security updates, logs, filesystem cache, and temporary build space.

| Tier | vCPU | Memory | SSD storage | Monthly transfer | Intended workload |
| --- | ---: | ---: | ---: | ---: | --- |
| Functional floor | 1 shared | 768 MB | 8 GB | 50 GB | Lab or short evaluation only |
| Production minimum | 1 | 1 GB | 15 GB | 100 GB | One public SSH sensor, 30-day retention |
| Recommended | 2 | 2 GB | 25 GB | 500 GB+ | Public sensor, gVisor, comfortable dashboard use |
| Higher traffic | 2–4 | 4 GB | 50 GB+ | 1 TB+ | Long retention or unusually high scan volume |

The supplied container is limited to 1 CPU, 512 MB RAM, 128 processes and 4,096 open files. Do not buy a 512 MB VPS for production: the host would have virtually no margin for Docker, package updates or traffic bursts. For a public deployment, **1 vCPU/1 GB/15 GB is the minimum purchase; 2 vCPU/2 GB/25 GB is the preferred default.**

Required hosting features:

- Dedicated public IPv4 address; IPv6 may remain disabled for the sensor
- Provider firewall capable of denying all new outbound connections while permitting replies
- Browser-based serial/recovery console independent of the VM network
- Encrypted SSD or block volume with snapshot support
- Ability to run a current 64-bit Linux distribution and Docker/Compose
- Accurate provider or host NTP
- No mandatory attachment to a private network, cloud role or instance identity

Recommended operating systems are a minimal, currently supported Ubuntu LTS or Debian stable image. The host does not need Python or a database server because the application and SQLite runtime are inside the container.

### Capacity planning

The decoy accepts text commands but no file uploads, malware samples or packet captures, so bandwidth and storage use are normally modest. Budget conservatively for **10 KB per captured session** after SQLite indexes, metadata and operational margin:

- 1,000 sessions/day × 30 days: approximately 300 MB of evidence
- 10,000 sessions/day × 30 days: approximately 3 GB of evidence
- Keep at least 5 GB free for the host, images, updates, temporary builds and logs

Actual usage depends on command volume and session duration. Alert at 70% disk utilization and stop accepting new traffic or shorten retention before reaching 85%. The default 30-day retention cleanup runs every six hours.

## 3. Pre-deployment checklist

- [ ] Dedicated, patched Linux VM with automatic security updates
- [ ] Cloud instance metadata disabled or protected with the provider's strictest mode
- [ ] No cloud role, workload identity or service-account credentials attached
- [ ] No peering or routes to private/production networks
- [ ] Outbound deny rule, with only explicitly required update paths temporarily permitted
- [ ] Inbound decoy port allowed from the Internet
- [ ] Dashboard port blocked publicly
- [ ] Management access restricted to VPN or named IP addresses
- [ ] Encrypted data volume and an approved retention period
- [ ] Accurate NTP time synchronization for incident timelines
- [ ] Monitoring for disk pressure, container restarts and health-check failures

## 4. Configure secrets

```bash
cp .env.example .env
openssl rand -base64 36   # dashboard password
openssl rand -hex 32      # independent fingerprint key
chmod 600 .env
```

Put the generated values in `.env`. The fingerprint key creates a non-reversible correlation tag for repeated passwords or keys. Rotating it intentionally breaks that historical correlation.

Do not commit `.env`. Prefer a host secret manager where your container platform supports one.

## 5. Deploy

### Automated Ubuntu deployment

The repository-root `honeyview.sh` installer automates the steps in this section for a clean, dedicated Ubuntu LTS server. Use a reviewed release tag rather than a moving branch:

```bash
curl -fsSL https://raw.githubusercontent.com/CollectorofProjects/HoneyView/v1.0.0/honeyview.sh | sudo bash -s -- --ref v1.0.0 --accept-risk
```

This does not configure the provider firewall, VPN, recovery console, private-network routes, cloud identities, or legal approvals. Those preflight controls remain mandatory. Review remote scripts before executing them as root.

### Manual deployment

```bash
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 honeyview
curl --fail http://127.0.0.1:8080/healthz
```

Apply the dedicated-VM egress rules and verify the isolation controls:

```bash
sudo ./deploy/harden-firewall.sh
./deploy/verify-isolation.sh
```

Persist the firewall rules using your distribution's supported mechanism and repeat the verification after every host, Docker, firewall or Compose change. The firewall script intentionally blocks the sensor subnet from initiating connections to the host and allows only reply traffic toward external clients.

For a stronger container boundary on a supported Linux host, install and register gVisor, then deploy with:

```bash
docker compose -f compose.yaml -f compose.gvisor.yaml up -d --build
```

gVisor adds a userspace-kernel boundary at a modest memory and performance cost. On a small VPS, run only HoneyView and essential management services. If gVisor is unavailable, the dedicated disposable VM remains the primary security boundary. Rootless Docker or Docker user-namespace remapping further reduces daemon/runtime risk, but verify published-port and firewall behavior in your provider before exposure.

To manage HoneyView with systemd, copy the repository to `/opt/honeyview`, install `deploy/honeyview.service` under `/etc/systemd/system/`, review its paths, then enable it. Keep `/opt/honeyview/.env` mode `0600`.

The default public decoy port is 2222. To use port 22, first move the host's real management SSH to a private interface or another tightly restricted port, verify recovery-console access, then set:

```dotenv
HONEYVIEW_PUBLIC_SSH_PORT=22
```

Restart with `docker compose up -d`. Do not use host networking.

## 6. Access the dashboard safely

The supplied deployment binds the dashboard to `127.0.0.1:8080`. Use an SSH tunnel from an approved analyst device:

```bash
ssh -L 8080:127.0.0.1:8080 your-admin-user@decoy-host
```

Then open `http://127.0.0.1:8080`. For a team deployment, put the dashboard behind a VPN and an HTTPS reverse proxy with individual identity, MFA and access logs. Basic authentication is a second layer, not an Internet-facing identity system.

## 7. Validate before exposure

From a separate test host:

```bash
ssh -p 2222 root@DECOY_IP
```

Enter a fake password, run `id`, `uname -a`, `ls`, and `exit`. Verify:

- The session appears immediately on the dashboard.
- Commands stream without refreshing.
- The command is stored, but no corresponding command runs on the VM.
- The dashboard remains unreachable from the public Internet.
- The container cannot reach a public URL or production address.
- Restarting the container preserves the database and SSH host key.

## 8. Reading the automation score

The score is a triage aid:

- **70–100:** likely automated
- **31–69:** uncertain
- **0–30:** likely human, once at least two commands exist

Signals include time to first command, median gap between commands, burst ratio, paste-like input speed, command chaining, downloader/temporary-path patterns, and many commands in a short period.

Do not label a person or claim AI involvement based only on this score. NAT, latency, terminal buffering, copy/paste, accessibility tools, red-team automation and human-operated frameworks all affect timing.

## 9. Daily operations

Use **Stop sensor** in the dashboard toolbar, then click **Confirm stop** within five seconds, to stop accepting new SSH connections. HoneyView closes active decoy sessions and keeps the dashboard and collected evidence online. Use **Start sensor** to resume listening.

This control is intended for maintenance and ordinary operations. If the host or container may be compromised, immediately block the sensor port in the cloud-provider firewall or security group, preserve evidence, and follow the incident procedure below.

1. Check `/healthz`, container state, disk use and clock synchronization.
2. Review likely automated sessions first, then uncertain high-command-count sessions.
3. Correlate source IPs and indicators with firewall, WAF and production authentication logs. Do not retaliate or probe the source.
4. Export notable indicators into your approved SIEM/ticketing workflow; minimize attacker-supplied content in notifications.
5. Treat captured commands as untrusted text. Do not paste them into a shell.
6. Document findings, confidence and time zone in incident records.

## 10. Evidence retention and privacy

`HONEYVIEW_RETENTION_DAYS` defaults to 30. Cleanup runs every six hours. SQLite files live in the `honeyview-data` volume.

- Restrict volume and backup access to the security team.
- Encrypt snapshots and define deletion schedules for backups too.
- Never publish raw IPs, usernames or command transcripts without review.
- If evidence is required for an investigation, take a read-only snapshot and record its SHA-256 hash, time, operator and storage location.
- Passwords are not stored. A keyed HMAC, length and username are recorded for correlation.

## 11. Backup and restore

Stop writes before making a simple volume copy:

```bash
docker compose stop honeyview
docker run --rm -v honeyview_honeyview-data:/data:ro -v "$PWD/backups:/backup" alpine \
  sh -c 'cp /data/honeyview.sqlite3 /backup/honeyview.sqlite3'
docker compose start honeyview
sha256sum backups/honeyview.sqlite3
```

Store backups outside the decoy VM. Test restoration periodically. For continuous backup, use SQLite's online backup API rather than copying a live database file.

## 12. Updating

```bash
docker compose build --pull --no-cache
docker compose run --rm honeyview python -m pytest  # when tests are included in your build pipeline
docker compose up -d
docker compose logs --tail=100 honeyview
```

Review dependency release notes and vulnerability advisories before updating. Pin the base-image digest in your deployment pipeline after verifying it in your trusted registry; the repository uses a readable image tag so it does not pretend an unverified digest is trustworthy.

## 13. Incident procedure

If HoneyView itself may be compromised:

1. Do not interact with the attacker.
2. Remove public ingress at the cloud firewall; do not merely stop the process.
3. Snapshot the VM and data volume using your provider's forensic procedure.
4. Record UTC time, observable symptoms, image version and operator actions.
5. Rebuild from a clean image in a new VM. Do not return the suspect host to service.
6. Rotate dashboard and fingerprint secrets and any management credentials that could have touched the host.
7. Investigate any attempted outbound connections or traffic toward private networks as a high-priority containment event.

## 14. Known limits

- This version emulates SSH only; it is not a malware sandbox.
- Uploaded binaries are not accepted or executed.
- Geolocation and ASN enrichment are intentionally absent to avoid external data sharing and lookup dependencies.
- It does not assert that an actor is human, a bot or an AI agent with certainty.
- SQLite is appropriate for a single sensor. Multiple sensors should send events to a centralized, authenticated collector rather than share a database volume.
