# Cardano node candidates on RockPi 4B

Target hardware: **4 GiB RAM**, **32 GiB eMMC**, `aarch64`. OS + cot-r management
leave roughly **2.5–3 GiB** for a node process under load. Prefer staying on 32 GiB
storage unless a measured chain footprint forces a larger module.

## Fit summary

| Implementation | Lang | aarch64 | RAM (published / observed) | Storage note | Wizard status |
|---|---|---|---|---|---|
| [Amaru](https://github.com/pragma-org/amaru) | Rust | Yes (deb/rpm) | Official **2 GiB** min; Pi/community often **&lt;1–1.4 GiB** under pressure | Current mainnet Mithril window **~10 GiB** (fits 32 GiB eMMC with headroom); full history needs larger disks | **Wizard 3.1–3.3** |
| [Dingo](https://github.com/blinklabs-io/dingo) | Go | Yes (release + npm) | BP sizing target **≤1 GiB** RSS (`core` mode ~0.5–0.7 GiB in benches) | Testnet-first; mainnet BP not ready | Catalog later |
| [Yano](https://github.com/bloxbean/yano) | Java | Needs verify | Quarkus/JDK 25; JVM heap fights 4 GiB unless **GraalVM native** | RocksDB chain; measure before enabling | Catalog later |
| [Gerolamo](https://github.com/HarmonicLabs/gerolamo) | TypeScript (Bun) | Needs verify | No published envelope yet; Bun + SQLite likely lighter than JVM | Soft ledger / Mithril hydrate; measure tip DB size | Catalog later |

Haskell `cardano-node` (classic) is **out of scope** here: typical **16–24 GiB** RAM.

## Amaru (priority)

- Upstream: [pragma-org/amaru](https://github.com/pragma-org/amaru) · guide: [mainnet install](https://amaru.global/user-guide/mainnet/)
- Wizard installs official **aarch64 `.deb`**, uses `/etc/default/amaru`, `amaru.service`, `amaru-bootstrap.service`
- Default network: **mainnet** (~10 GiB Mithril window today)
- Master bootstraps via uplink; **LAN-copies** DBs to adopted peers (no second Mithril download)
- Auto-start off by default for venue demos; toggle in wizard 3.3
- Post-start observe UI in wizard 3.3 (journal projection); see [amaru-operator-ui.md](amaru-operator-ui.md)
- Networking note: duplex-on-outbound / WireGuard still later for NAT relays

## Dingo

- Upstream: [blinklabs-io/dingo](https://github.com/blinklabs-io/dingo)
- Strong arm64 story and low-memory `storageMode=core` profile
- Explicitly **not** for mainnet block production yet → fine for demo relays later

## Yano

- Upstream: [bloxbean/yano](https://github.com/bloxbean/yano) ([getyano.dev](https://getyano.dev))
- Data node / Quarkus; native image is the realistic path on 4 GiB
- Needs an aarch64 native artifact size + RSS measurement before wizard enable

## Gerolamo

- Upstream: [HarmonicLabs/gerolamo](https://github.com/HarmonicLabs/gerolamo)
- Bun runtime + SQLite soft ledger; embed-friendly
- Needs RockPi RSS + DB growth measurement before wizard enable

## Cluster roles (wizard later)

| Role | Intent |
|---|---|
| **Relay** | Outbound to public topology; LAN peer to neighbours; WireGuard for a stable public IP:port behind NAT |
| **Producer** | Awaits stake-pool secrets / wallets (last wizard stage); LAN peers to local relays |

LAN peering: each board talks to the other two on reserved addresses
(`.194` / `.195` / `.196`). Relays additionally dial the global network; inbound
from the public Internet is not assumed without WireGuard or duplex reuse of an
outbound TCP session.
