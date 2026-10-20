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

**Never copy another board's `device.env`.** A shared example must not assign `.196`
to every RockPi (that caused an IP clash when preparing RockPi 1).

Identity is resolved at `prepare` time, in order:

1. `sudo ./scripts/cot-r prepare --reserved-ipv4 172.27.213.195 --device-id rockpi-1`
2. MAC map `/etc/cot-r/device-macs.map` (from `config/templates/device-macs.map.example`)
3. Existing `device.env` (interactive confirm when MAC is not in the map)
4. Interactive menu (TTY) choosing `.195` / `.196` / `.194`

`prepare` also sets the Linux hostname to `COT_R_DEVICE_ID` (`rockpi-1` /
`rockpi-2` / `rockpi-3`) and updates the `127.0.1.1` line in `/etc/hosts`.

- **AP enabled:** static address on `br0` (MAC still = `end0` permanent MAC).
- **AP disabled:** static address on `end0` via `00-cot-r-end0-static.network`.

SSID is `Cardano-Rocks 1|2|3` from board id / reserved IP (short enough for OLED Wi-Fi QR).

## Commands

```bash
./scripts/cot-r net-status
cat /sys/class/net/end0/address   # add this MAC to device-macs.map
sudo ./scripts/cot-r prepare --reserved-ipv4 172.27.213.195 --device-id rockpi-1
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
