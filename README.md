# HoneyView

HoneyView is an isolated SSH decoy with a real-time defensive dashboard. It accepts fake SSH logins, presents a simulated Ubuntu shell, records attacker behavior, and estimates whether a session is likely automated from timing and command-shape signals.

The authenticated dashboard includes a **Start/Stop sensor** control. Stopping requires a second confirmation click, closes the public SSH listener, and disconnects active decoy sessions while leaving the dashboard and stored evidence available. Starting it restores the listener without restarting HoneyView.

> **Authorized defensive use only.** Internet-facing honeypots carry security, privacy, and legal risk. The deployer assumes responsibility for authorization, isolation, operation, collected data, and compliance. HoneyView is provided without warranty and no isolation control is represented as unbreakable. Read [DISCLAIMER.md](DISCLAIMER.md) and [RUNBOOK.md](RUNBOOK.md) before use.

It **never executes captured commands**. The decoy shell is a deterministic parser backed by fictional files and responses.

## What it captures

- Source IP and port
- Username and authentication method
- Password length and a keyed, truncated HMAC for reuse correlation (never plaintext)
- Session open/close times
- Commands, typing duration, and inter-command delay
- A 0–100 automation-likelihood score with its contributing reasons
- Live session and command events over an authenticated SSE dashboard

The classifier cannot prove that an AI agent is involved. It distinguishes human-paced behavior from fast, bursty or automation-shaped behavior. Copy/paste, scripts, bots and AI agents can overlap.

## One-command Ubuntu deployment

After uploading the repository, the current `main` branch can be installed with:

```bash
curl -fsSL https://raw.githubusercontent.com/CollectorofProjects/HoneyView/main/honeyview.sh | sudo bash -s -- --accept-risk
```

For production, create and review a release tag, then pin both the downloaded installer and installed source to it:

```bash
curl -fsSL https://raw.githubusercontent.com/CollectorofProjects/HoneyView/v1.0.0/honeyview.sh | sudo bash -s -- --ref v1.0.0 --accept-risk
```

The installer supports clean 64-bit Ubuntu 22.04, 24.04, and 26.04 LTS servers on amd64 or arm64. It:

- installs Docker Engine and the Compose plugin from Docker's signed apt repository when needed;
- installs HoneyView under `/opt/honeyview` from the specified branch or release tag;
- generates separate random dashboard and fingerprint secrets with root-only permissions;
- builds the container, applies the sensor egress/host-access firewall, and installs systemd services;
- keeps the dashboard bound to `127.0.0.1:8080`; and
- starts HoneyView and refuses to finish if health or isolation verification fails.

The default public decoy port is `2222`. Add `--ssh-port 22` only after moving management SSH and confirming independent recovery-console access.

Downloading a script and executing it as root is convenient but high trust. For a production deployment, inspect the exact tagged script first:

```bash
curl -fsSL https://raw.githubusercontent.com/CollectorofProjects/HoneyView/v1.0.0/honeyview.sh -o /tmp/honeyview.sh
less /tmp/honeyview.sh
sudo bash /tmp/honeyview.sh --ref v1.0.0 --accept-risk
```

The repository must be public for these unauthenticated download commands. `--repo` remains available for reviewed mirrors or forks.

The installer cannot configure the hosting provider's firewall. Before exposure, allow only the decoy port publicly, keep port `8080` blocked, restrict real management SSH, and deny new outbound traffic at the provider layer while allowing replies.

## Quick local start

```bash
cp .env.example .env
# Replace both secrets in .env before continuing.
docker compose up --build -d
```

- Decoy SSH: `ssh -p 2222 root@localhost` (any non-empty password)
- Dashboard: `http://127.0.0.1:8080`
- Health check: `http://127.0.0.1:8080/healthz`

The dashboard uses HTTP Basic authentication from `.env`. It is intentionally bound only to loopback by the supplied Compose file.

Dashboard sensor controls require an authenticated same-origin request and a dedicated action header. They are for routine operations, not emergency containment. If compromise is suspected, block the public sensor port at the cloud-provider firewall first.

For an Internet-facing sensor, apply `deploy/harden-firewall.sh`, run `deploy/verify-isolation.sh`, and follow every preflight item in the runbook. The production design assumes a dedicated disposable VPS with no production connectivity; a container by itself is not treated as an adequate trust boundary.

## Server sizing

| Deployment | CPU | RAM | Disk | Suggested use |
| --- | ---: | ---: | ---: | --- |
| Functional floor | 1 shared vCPU | 768 MB | 8 GB SSD | Private evaluation only; little update or traffic headroom |
| Production minimum | 1 vCPU | 1 GB | 15 GB SSD | One Internet-facing SSH sensor, 30-day retention |
| Recommended | 2 vCPU | 2 GB | 25 GB SSD | Public deployment with comfortable analysis and update headroom |
| Higher traffic | 2–4 vCPU | 4 GB | 50+ GB SSD | Heavy scanning, longer retention, or multiple analysts |

Choose the **recommended** tier when using gVisor. The server also needs a public IPv4 address, an external recovery console, provider-level ingress and egress firewall controls, encrypted storage, and snapshots. A plan with at least 100 GB monthly transfer is ample for typical command-only telemetry; 1 TB included transfer avoids having to predict unusual scan volume.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
HONEYVIEW_DASHBOARD_PASSWORD=development-only-password python -m app.main
pytest
```

Container builds use `requirements.lock`, the fully resolved and hash-locked Python 3.14 production set. Regenerate it with `pip-compile --generate-hashes --output-file=requirements.lock requirements.txt`, then audit and retest whenever direct dependencies change. `SBOM.json` is the corresponding CycloneDX software bill of materials.

Read [RUNBOOK.md](RUNBOOK.md) before any Internet deployment.

## License and legal notice

Read [DISCLAIMER.md](DISCLAIMER.md). A disclaimer reduces ambiguity but cannot guarantee that an author will never be sued or held liable; enforceability depends on applicable law and the facts.

This repository does not yet include an open-source license because the copyright holder and license choice have not been provided. Before publishing for third-party installation, choose a license deliberately and have counsel review it. Without a license, default copyright restrictions generally remain in effect even when a repository is public.

## Architecture

```text
Internet scanners ──TCP/2222──> simulated SSH service
                                      │
                                      ├── events + timing
                                      ▼
                               SQLite evidence store
                                      │
Analyst VPN/SSH tunnel ──HTTP──> authenticated live dashboard
```

HoneyView is an observation tool, not an intrusion-prevention system. Never place it on a production host or network segment.
