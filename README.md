# Cardano on the Rocks

Three Radxa Rock Pi 4B boards, a MikroTik mAP, and SSD1306 OLEDs: a local demo and
onboarding platform for Cardano-related demonstrations on Armbian (AArch64).

**Source repository:** https://github.com/gufmar/cardano-on-the-rocks  
**Copyright:** Markus (GitHub: [gufmar](https://github.com/gufmar))  
**Licence:** [GNU AGPL v3](LICENSE)

> Attribution and a link to this repository belong in documentation and in any
> application About page. AGPL also requires offering corresponding source for
> modified versions that users interact with over a network. See [docs/licensing.md](docs/licensing.md).

## Current status

Incremental build. Working on RockPi 2 today:

- Bash prepare / status / project reset
- OLED display service (Unix socket)
- Deployment user + `/opt/cot-r/src` clone with update/rollback
- Management API with identity, boot ID, status, and demo reset
- Bridged AP tooling (`ap-prepare` / `ap-enable --confirm` / `ap-disable`)

Next: peer coordination, RouterOS uplink control, local setup UI, portal handoff.

Not yet implemented: association portal handoff, guest-portal flow, demo application.

## Hardware

| Item | Detail |
|---|---|
| Boards | 3× Radxa Rock Pi 4B (AArch64 / RK3399) |
| OS (verified on RockPi 2) | Armbian_community 26.11 rolling, Debian 13 (trixie) |
| Kernel (verified) | `6.18.56-current-rockchip64` |
| Display | 0.96" SSD1306 OLED 128×64 on header pins 1/3/5/6 (I²C7) |
| Router | MikroTik mAP RBmAP2nD, RouterOS 7.21.4 long-term |
| LAN | `172.27.213.0/24`, gateway `172.27.213.1` |

Reserved Ethernet addresses: RockPi 1 `.195`, RockPi 2 `.196`, RockPi 3 `.194`.

## Installation (baseline preparation)

On a RockPi with this repository cloned:

```bash
./scripts/cot-r status          # inspection only
sudo ./scripts/cot-r prepare    # idempotent system preparation
./scripts/cot-r status          # verify
```

If the I²C7 overlay was newly added, reboot once when convenient, then:

```bash
sudo ./scripts/cot-r prepare    # finish OLED validation after reboot
sudo ./scripts/cot-r oled-test
```

Details: [docs/provisioning.md](docs/provisioning.md), [docs/oled.md](docs/oled.md).

## Development

- Primary development host: RockPi 2 (`172.27.213.196`).
- Preserve SSH, Ethernet, existing users, and this development clone.
- Prefer Debian packages over ad-hoc pip installs for system services.
- Keep device secrets in `/etc/cot-r/`, never in git.

```bash
git clone https://github.com/gufmar/cardano-on-the-rocks.git
cd cardano-on-the-rocks
./scripts/cot-r status
```

## Deployment (planned)

Each device will have a dedicated deployment user and its own clone for install/updates.
Shared setup code stays device-agnostic; identity and credentials stay outside committed
files. Update/rollback procedures will land with the management service stage.

## Reset modes

| Mode | Command / API | Effect |
|---|---|---|
| Status / inspect | `cot-r status` | No changes |
| Prepare | `sudo cot-r prepare` | Apply or refresh baseline |
| Project reset | `sudo cot-r reset-project` | Remove project-owned install, reapply baseline |
| Demo reset | `POST /api/v1/reset` (later) | Clear demo session only |
| Identity wipe | explicit future command | Separate; not default |

Project reset does **not** reinstall the OS and does not erase unrelated data, admin
users, SSH keys, Ethernet DHCP identity, or development clones by default.

## Recovery

If software fails but the OS and network remain:

1. SSH to the reserved Ethernet address.
2. Run `./scripts/cot-r status` from the clone.
3. `sudo ./scripts/cot-r reset-project` to return to the prepared baseline.
4. If the OLED is missing, networking and SSH must still work.

There is no remote recovery after a failed OS, power loss with corrupt storage, or a
broken Layer-2 path; use console/physical access in those cases.

## Bridged AP (explicit; after prepare)

```bash
./scripts/cot-r ap-status
sudo ./scripts/cot-r ap-prepare
sudo ./scripts/cot-r ap-enable --confirm   # short network blip possible
./scripts/cot-r ap-status
```

See [docs/ap-bridging.md](docs/ap-bridging.md). PSK stays in `/etc/cot-r/ap.psk`.

## Management API (after prepare)

```bash
TOKEN=$(sudo cat /etc/cot-r/api.token)
# Use -sS so connection errors are visible (plain -s hides them).
curl -sS --fail-with-body http://127.0.0.1:8080/api/v1/health; echo
curl -sS --fail-with-body -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8080/api/v1/status; echo
curl -sS --fail-with-body -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"scope":"demo"}' \
  http://127.0.0.1:8080/api/v1/reset; echo
```

## Documentation

| Doc | Topic |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components and state model |
| [docs/networking.md](docs/networking.md) | LAN, mAP, AP bridging plans |
| [docs/oled.md](docs/oled.md) | I²C7, display service, QR limits |
| [docs/provisioning.md](docs/provisioning.md) | Prepare / reset behaviour |
| [docs/deployment.md](docs/deployment.md) | Deploy user, update, rollback |
| [docs/ap-bridging.md](docs/ap-bridging.md) | Planned bridged AP design |
| [docs/licensing.md](docs/licensing.md) | AGPL evaluation and tradeoffs |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |

## Power

Devices are powered via PoE switch/injector (injector shows total draw). Prefer
lightweight services and bounded OLED frame rates. Measure consumption around
substantial changes when practical. Do not trade away reliable networking or recovery.
