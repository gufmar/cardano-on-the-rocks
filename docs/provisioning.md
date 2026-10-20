# Provisioning and reset

## Entry point

```bash
./scripts/cot-r <command> [options]
```

| Command | Mutates system? | Purpose |
|---|---|---|
| `status` | No | Inspect compatibility, network, I²C, services, tracked state |
| `prepare` | Yes (needs root) | Idempotent baseline preparation; optional `--reserved-ipv4` / `--device-id` |
| `reset-project` | Yes (needs root) | Remove project-owned install, restore tracked changes, re-prepare |
| `oled-test` | Mild | Probe bus and optionally draw a test pattern (needs prepared OLED) |
| `deploy-status` | No | Show deploy clone and recorded commit |
| `update [ref]` | Yes (needs root) | Fetch/checkout deploy clone and install runtime |
| `rollback` | Yes (needs root) | Reinstall previous recorded commit |
| `ap-status` | No | Wi-Fi AP / bridge inspection |
| `ap-prepare` | Yes (needs root) | Install hostapd/iw; SSID/PSK/configs (`--rotate-psk` renews password) |
| `ap-enable --confirm` | Yes (needs root) | Apply bridged AP + start hostapd |
| `ap-disable --confirm` | Yes (needs root) | Remove AP bridge; restore Ethernet static |
| `ap-show-psk` | Mild (needs root) | Print SSID/PSK/ASCII QR; push Wi-Fi QR to OLED (`--no-qr` / `--no-oled`) |
| `net-status` | No | Reserved vs live management IPv4 |
| `net-set-address` | Yes (needs root) | Set reserved IP and apply static addressing |
| `version` | No | Print tool and repository version |

Never unexpectedly restarts networking or reboots. If a boot overlay change needs a
reboot, the script **reports** it and exits successfully after staging the change.

## Usual setup sequence

```bash
./scripts/cot-r status
sudo ./scripts/cot-r prepare --reserved-ipv4 172.27.213.195 --device-id rockpi-1
# reboot once if I2C7 overlay was newly added, then re-run prepare
sudo ./scripts/cot-r ap-prepare
sudo ./scripts/cot-r ap-enable --confirm
sudo ./scripts/cot-r ap-show-psk    # phone join: scan QR or type SSID/PSK
./scripts/cot-r ap-status
./scripts/cot-r net-status
```

Re-print Wi-Fi credentials later with the same `ap-show-psk` command. PSK file:
`/etc/cot-r/ap.psk`.

## What `prepare` does

1. Inspect architecture (expect `aarch64`), Armbian/Debian release, kernel, boot config, network stack.
2. Record initial snapshot under the project state directory.
3. Refresh apt metadata and install a small baseline tool set (plus OLED deps when enabling display).
4. Create project-owned users, directories, and permissions.
5. Resolve **this board's** reserved management IP (CLI, MAC map, confirm, or prompt). Never seed `.196` from a shared example.
6. Enable I²C7 overlay for the OLED header if needed (backup `armbianEnv.txt` first).
7. Install udev rules, display unit, and config templates.
8. Validate what can be validated without reboot; report remaining steps.

First board prepare examples:

```bash
# RockPi 1 (explicit; safest when recovering from a wrong static IP)
sudo ./scripts/cot-r prepare --reserved-ipv4 172.27.213.195 --device-id rockpi-1

# Or add end0 MAC to /etc/cot-r/device-macs.map, then:
sudo ./scripts/cot-r prepare
```

### Baseline packages

Requested common tools (verified available on Debian trixie / this image):

```bash
htop pydf mtr net-tools inetutils-traceroute curl jq git
```

OLED / display foundation (installed by prepare when display support is enabled):

```bash
i2c-tools python3-luma.oled python3-luma.core python3-pil python3-smbus2 python3-qrcode
```

Further dependencies are added only as features land. No large speculative stack.

## Paths (runtime, outside the git clone)

| Path | Purpose |
|---|---|
| `/etc/cot-r/` | Device config and secrets (mode `0750` / files `0640`) |
| `/var/lib/cot-r/` | Durable project state, package manifest, file inventory |
| `/var/lib/cot-r/backups/` | Timestamped backups of files before modification |
| `/var/log/cot-r/` | Preparation and service logs |
| `/opt/cot-r/` | Installed runtime copy of display/management code |
| `/run/cot-r/` | Runtime sockets and boot-ephemeral state |

The development clone (for example `/home/mgu/git/cardano-on-the-rocks` on RockPi 2)
is **not** deleted by `reset-project` unless an explicit flag is used later.

## Users

| User | Purpose |
|---|---|
| `cot-r-display` | Runs the OLED service (supplementary group for I²C access) |
| `cot-r` | Deployment / management service user |

See also [deployment.md](deployment.md).

Existing administrative users (for example `mgu`) and SSH access are preserved.

## Idempotency and recovery

- Separate inspection (`status`) from mutation (`prepare` / `reset-project`).
- Track project-managed files and packages in `/var/lib/cot-r/manifest.json`.
- Back up files before first modification.
- Avoid duplicate overlay entries in `armbianEnv.txt`.
- After partial failure, re-running `prepare` continues safely.
- Do not purge arbitrary packages; remove only project-owned components that the
  manifest shows are not required by the baseline.

## `reset-project` vs demo reset

**Project reset** (`cot-r reset-project`):

- Stops project systemd units
- Removes `/opt/cot-r` application payload and disposable generated config
- Restores tracked system files from backups where recorded
- Re-runs baseline `prepare`
- Preserves OS, unrelated software, admin users, SSH keys, Ethernet/DHCP identity,
  development clones, and durable identity by default

**Demo reset** (REST `POST /api/v1/reset`, not yet implemented):

- Clears demo session only
- Keeps management service, network identity, AP credentials, peer trust, router credentials

## Deployment clones (planned)

Each RockPi will use a dedicated deployment user and its own clone under a stable path
(documented when that stage lands). Device-specific identity stays in `/etc/cot-r/`,
not in committed source.

## Safety constraints

- Preserve administrator SSH authorized keys and sudo access.
- Do not restart `systemd-networkd` or reboot as part of normal prepare.
- Keep secrets out of the public repository.
