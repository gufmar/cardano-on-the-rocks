# Stage plan (revised)

Foundation onboarding (RockPi bridged AP + OLED QR + local `/setup` wizard) replaces
the original captive-portal stages. Stages 11–12 from the first briefing are
**skipped**.

## Done (foundation)

| # | Item |
|---|---|
| 1–7 | Repo, prepare/reset, OLED, deploy, management API, bridged AP |
| 8 | Peer coordination (claim / lease / Waiting) |
| 9 | RouterOS uplink control via per-node SSH |
| 10 | Local setup wizard (confirm → uplink → adopt/release) |

## In progress / next

| # | Item | Notes |
|---|---|---|
| **A** | Wizard 3.1–3.3 Amaru | Install `.deb`, Mithril bootstrap, `/etc/default/amaru` editor |
| **B** | LAN snapshot to adopted | Master exports DB; peers pull over LAN |
| **C** | Auto-start switch | Per board; default off for venue demos |
| **C2** | Post-start observe UI | Journal projection + Overview/Sync/Peers/Host in 3.3 |
| **C3** | Observe depth | Richer peer rates / schema tracking as Amaru evolves |
| **C4** | Catch-up tune | `AMARU_LOG=info` + MemoryHigh=3G on 4 GiB |
| **C5** | OLED Nodes carousel | Progress / CPU / mem / tip / mempool slides |
| **D** | Roles + LAN peers | relay / producer; fixed neighbour peering |
| **E** | Relay WireGuard egress | Public IP:port behind NAT / hotel Wi‑Fi |
| **F** | Producer secrets UI | Stake pool keys / wallets |
| **G** | More catalog entries | Dingo, Yano, Gerolamo when fit proven |

## Hardware envelope (hard)

| Resource | Constraint |
|---|---|
| RAM | 4 GiB soldered (OS + mgmt + node must share) |
| Storage | 32 GiB eMMC today (Amaru mainnet window ~10 GiB; larger disks before full history / other nodes) |
| Arch | `aarch64` / Armbian |

## Wizard flow

1. Start (confirm)
2. Internet uplink (mAP)
3. Neighbour nodes (adopt/release)
4. **Nodes** — 3.1 Install · 3.2 Bootstrap · 3.3 Config
5. Later: roles, WireGuard, producer secrets

See [setup-wizard.md](setup-wizard.md) and [node-candidates.md](node-candidates.md).
