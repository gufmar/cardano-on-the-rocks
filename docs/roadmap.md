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

Association reporting (hostapd STA table) remains as an optional diagnostic API.
It is **not** required for setup now that phones join the RockPi AP and open
`http://<node>:8080/setup` directly.

## Next

| # | Item | Notes |
|---|---|---|
| **A** | Wizard step 3: node catalog | Sneak peek now; Amaru selectable first |
| **B** | Amaru deploy package | aarch64 install, systemd, data dir under `/var/lib/cot-r/` |
| **C** | Per-board implementation + role | User picks Amaru/Dingo/… and **relay** or **producer** per RockPi |
| **D** | LAN fixed peerings | Each node peers with its two neighbours on `172.27.213.0/24` |
| **E** | Relay: public topology + outbound | Relays dial the global network; inbound only via duplex on outbound TCP |
| **F** | Relay: WireGuard egress endpoint | Outbound WG tunnel → public IP + fixed port bound to WG identity (NAT / hotel Wi‑Fi) |
| **G** | Producer secrets UI | Stake pool keys / wallets via wizard (latest) |
| **H** | Enable more catalog entries | Dingo, then Yano / Gerolamo when fit is proven |

## Hardware envelope (hard)

| Resource | Constraint |
|---|---|
| RAM | 4 GiB soldered (OS + mgmt + node must share) |
| Storage | 32 GiB eMMC today (swappable, prefer staying here) |
| Arch | `aarch64` / Armbian |

Full mainnet history will not fit on 32 GiB. Prefer Mithril / tip-oriented
bootstrap, or preview/preprod first. See [node-candidates.md](node-candidates.md).

## Wizard flow (target)

1. Start (confirm)
2. Internet uplink (mAP)
3. Neighbour nodes (adopt/release)
4. **Node deployment** (implementation + role per board) ← sneak peek in UI now
5. Later: producer secrets / WireGuard relay publish
