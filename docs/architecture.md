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
| `scripts/cot-r` | System preparation, status, project reset | Milestone 1 |
| Display service | Sole owner of OLED hardware; local API | Milestone 1 |
| Management service | Identity, state machine, demo reset API | Later |
| Bridged AP + portal | Onboarding SSID and captive handoff | Later |
| Peer coordination | Coordinator lease, heartbeats | Later |
| RouterOS client | Uplink scan/configure (backend only) | Later |
| Demo application | Cardano demo after foundations | Later |

## Process isolation

- Prefer non-root services.
- Use narrowly scoped privileged helpers for system changes (udev, overlays, packages).
- Only one process owns `/dev/i2c-*` for the OLED.
- Management survives demo-application failure; demo reset must not depend on the demo process.

## State model (planned)

States: `Initial` → `Waiting` → `Configuring` → `Ready`, with `Recovery` and `Resetting`.

Each management start generates a fresh **boot ID**. Controllers must treat a restarted
peer as needing deliberate reprovisioning even if the IP is unchanged.

## Reset scopes

| Reset | Effect |
|---|---|
| **Demo reset** (`POST /api/v1/reset`, later) | Clear demo session / disposable app config; keep management, network identity, AP credentials, peer trust, router credentials |
| **Project reset** (`cot-r reset-project`) | Stop project services; remove project-owned app bits; restore tracked system changes; reapply baseline preparation |
| **Identity reprovision** (explicit, separate) | Full wipe of durable identity/recovery credentials when intentionally requested |

Project reset is not an OS reinstall and cannot undo unrelated administrator changes.

## Power and reliability

Prefer lightweight services, bounded OLED frame rates, and no busy polling. Do not
sacrifice SSH, Ethernet, or recovery for speculative power savings.
