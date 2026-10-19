# Networking

## Observed host (RockPi 2, development)

Verified on the live device during initial scaffolding:

| Item | Value |
|---|---|
| Hostname | `rockpi-4b` |
| Architecture | `aarch64` / `arm64` |
| OS | Armbian_community 26.11.0-trunk.95 (Debian 13 trixie) |
| Kernel | `6.18.56-current-rockchip64` |
| Ethernet | `end0` → `172.27.213.196/24` (matches RockPi 2 reservation) |
| Default route | via `172.27.213.1` |
| Wi-Fi | `wlan0` present (`brcmfmac` / BCM4329-compatible SDIO), currently DOWN |
| Network stack | `systemd-networkd` active; Netplan file `10-dhcp-all-interfaces.yaml` |
| NetworkManager | inactive |
| SSH | listening on port 22 |

Preparation scripts must use Armbian/systemd-networkd mechanisms. Do not replace
networking with Raspberry Pi or NetworkManager assumptions.

## MikroTik mAP (owner-reported; verify before changing)

| Item | Configuration |
|---|---|
| Model | MikroTik mAP RBmAP2nD |
| RouterOS | 7.21.4 long-term |
| Wireless | Legacy `/interface wireless`, single 2.4 GHz radio |
| LAN bridge | `bridge` with `ether1`, `ether2` |
| Gateway / DNS | `172.27.213.1` |
| LAN | `172.27.213.0/24` |
| DHCP pool | `172.27.213.100`–`172.27.213.199` |
| Upstream | `wlan1` station, security profile `uplink`, DHCP client |
| Virtual AP | `wlan-cardano`, SSID `Cardano-on-the-Rocks` (planned to disable) |

Preserve LAN, reservations, bridge, firewall/NAT, upstream DHCP, and PoE settings.
Inspect before applying:

```routeros
/interface wireless disable wlan-cardano
```

Verify absence of legacy `192.168.88.1/24` management addressing.

## RockPi AP design (bridged)

Prefer **bridging** the RockPi AP to Ethernet (see [ap-bridging.md](ap-bridging.md)):

1. Phone joins RockPi SSID.
2. Client DHCP comes from the mAP.
3. Gateway remains `172.27.213.1`.
4. RockPi management address moves onto `br0` with the permanent `end0` MAC.
5. No extra DHCP server on the bridge.
6. mAP continues Internet NAT.

Enable only via `cot-r ap-enable --confirm` (not baseline prepare).

Coordinator keeps its AP; peers disable theirs only after acknowledging a valid coordinator.

## Association reporting (planned)

On a shared bridge the mAP sees the phone MAC/IP, not which RockPi AP served it.
Each RockPi must report authenticated association events for portal handoff.

VLAN-per-AP is an agreed fallback only if shared-LAN association reporting fails
reliability requirements. Do not introduce VLANs silently.

## Secrets

RouterOS credentials, AP PSKs, and uplink secrets live under device-local
`/etc/cot-r/` (or equivalent), never in this public repository.
