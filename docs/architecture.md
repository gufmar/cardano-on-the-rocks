# Architecture (foundation stage)

## Scope of this document

This describes the **intended** multi-device architecture and what exists in the
repository **today**. Later networking, peer coordination, and demo application
pieces are planned; they are not assumed to be present until implemented.

## Hardware topology

```text
[Upstream Wi-Fi] <--station-- [MikroTik mAP] <--Ethernet/PoE switch--> [RockPi 1]
                                      |                                [RockPi 2]
                                      +--------------------------------[RockPi 3]
                                      each RockPi: SSD1306 OLED on I2C
```

| Device | Reserved Ethernet address (mAP DHCP) |
|---|---|
| RockPi 1 | `172.27.213.195` |
| RockPi 2 | `172.27.213.196` |
| RockPi 3 | `172.27.213.194` |
| mAP gateway/DNS | `172.27.213.1` |

Authenticate device identity independently of IP address. Reservations are discovery
hints, not trust.

## Logical components (incremental)

| Component | Role | Stage |
|---|---|---|
| `scripts/cot-r` | System preparation, status, project reset, update | Done |
| Display service | Sole owner of OLED hardware; local API | Done |
| Management service | Identity, boot ID, state, demo reset API | Done (foundation) |
| Deploy clone + update | `/opt/cot-r/src`, rollback via `deploy.json` | Done (foundation) |
| Bridged AP (`ap-prepare` / `ap-enable`) | Bridge wlan0 to end0; mAP DHCP | Done (foundation) |
| OLED onboarding | Steps 1–3 (Wi‑Fi QR → setup URL → gear) | Done (foundation) |
| Setup wizard | Confirm, uplink via mAP SSH, adopt/release peers | Done (stage 10) |
| Peer coordination | Coordinator lease, heartbeats | Done (stage 8) |
| Captive portal / guest portal | Original briefing 11–12 | **Skipped** (RockPi AP + OLED QR) |
| Association reporting | Optional hostapd STA diagnostic | Optional (not required for setup) |
| Node catalog (wizard step 3) | Choose Amaru / Dingo / … per board | Next (sneak peek in UI) |
| Amaru deploy | aarch64 package + systemd on RockPi | Next |
| Roles + LAN peers | relay / producer; fixed neighbour peering | Later |
| Relay WireGuard egress | Public IP:port behind NAT / hotel Wi‑Fi | Later |
| Producer secrets UI | Stake pool keys / wallets | Later |

Full stage table: [roadmap.md](roadmap.md). Candidate fit: [node-candidates.md](node-candidates.md).

## Process isolation

- Prefer non-root services.
- Use narrowly scoped privileged helpers for system changes (udev, overlays, packages).
- Only one process owns `/dev/i2c-*` for the OLED.
- Management survives demo-application failure; demo reset must not depend on the demo process.

## State model

States: `Initial` → `Waiting` → `Configuring` → `Ready`, with `Recovery` and `Resetting`.

Each management start generates a fresh **boot ID** under `/run/cot-r/boot.id`.
Controllers must treat a restarted peer as needing deliberate reprovisioning even if
the IP is unchanged. Durable device id lives in `/etc/cot-r/device.id`.

Ended demo sessions are recorded so stale peers cannot resurrect an old controller
session after reset.

## Management API (foundation + peers)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/health` | no | Liveness |
| GET | `/api/v1/status` | API Bearer | Identity, boot ID, state, role, deploy commit |
| GET | `/api/v1/about` | API Bearer | Attribution and licence |
| GET | `/api/v1/peers` | API Bearer | Discover reserved peers |
| GET | `/api/v1/associations` | API Bearer | Local hostapd stations (MAC, generation) |
| GET | `/api/v1/association/resolve?mac=` | API Bearer | Cluster-wide: which RockPi serves MAC |
| POST | `/api/v1/setup/claim` | API Bearer | Become coordinator; announce to peers |
| POST | `/api/v1/reset` | API Bearer | Demo reset (`scope=demo` only) |
| GET | `/api/v1/peer/hello` | peer Bearer | Peer identity exchange |
| GET | `/api/v1/peer/association/lookup?mac=` | peer Bearer | Local MAC lookup for peers |
| POST | `/api/v1/peer/claim` | peer Bearer | Accept coordinator claim → Waiting |
| POST | `/api/v1/peer/heartbeat` | peer Bearer | Refresh Waiting lease |
| POST | `/api/v1/peer/release` | peer Bearer | Session ended → Initial |

Default listen: `0.0.0.0:8080`.

Tokens: `/etc/cot-r/api.token` (operator), `/etc/cot-r/peer.token` (shared cluster).

Details: [peers.md](peers.md).

## Reset scopes

| Reset | Effect |
|---|---|
| **Demo reset** (`POST /api/v1/reset`) | Clear demo session / disposable app config; keep management, network identity, AP credentials, peer trust, router credentials |
| **Project reset** (`cot-r reset-project`) | Stop project services; remove project-owned app bits; restore tracked system changes; reapply baseline preparation |
| **Identity reprovision** (explicit, separate) | Full wipe of durable identity/recovery credentials when intentionally requested |

Project reset is not an OS reinstall and cannot undo unrelated administrator changes.

## Power and reliability

Prefer lightweight services, bounded OLED frame rates, and no busy polling. Do not
sacrifice SSH, Ethernet, or recovery for speculative power savings.
