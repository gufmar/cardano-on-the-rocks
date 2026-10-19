# Stable addressing for the three RockPis

## Problem

MikroTik DHCP reservations can stop matching after the management address moves from
`end0` onto `br0` (bridged AP). systemd-networkd’s DHCP client-id / IAID can change,
so the mAP hands out a different dynamic address (for example `.193` instead of `.196`).
Cursor/SSH then still targets the old reserved IP and fails.

## Chosen approach: static management IPs on each RockPi

| Device | Fixed address |
|---|---|
| RockPi 1 | `172.27.213.195/24` |
| RockPi 2 | `172.27.213.196/24` |
| RockPi 3 | `172.27.213.194/24` |
| Gateway / DNS | `172.27.213.1` (mAP) |

Configured in `/etc/cot-r/device.env` as `COT_R_RESERVED_IPV4` (plus gateway/DNS/prefix).

- **AP enabled:** static address on `br0` (MAC still = `end0` permanent MAC).
- **AP disabled:** static address on `end0` via `00-cot-r-end0-static.network`.

SSID suffix uses the **reserved** address (`.196`), not whatever dynamic lease last appeared.

## Commands

```bash
./scripts/cot-r net-status
sudo ./scripts/cot-r net-set-address 172.27.213.196 rockpi-2
# if AP is already on:
sudo ./scripts/cot-r ap-enable --confirm
```

## mAP DHCP pool recommendation

Keep the mAP as DHCP server for phones and guests, but **exclude** the three RockPi
addresses from the dynamic pool (or keep MAC reservations as a belt-and-suspenders
backup). Example intent:

- Pool usable range: `172.27.213.100`–`172.27.213.192` (or similar)
- Leave `.194` / `.195` / `.196` for the RockPis (static on device)

Optional RouterOS reservation-by-MAC remains useful if a board ever falls back to DHCP,
but static on-device addressing is the source of truth for management access.

## Identity vs address

Authenticate device identity independently of IP (`device.id` / keys). The fixed
addresses are for discovery and operator access only.
