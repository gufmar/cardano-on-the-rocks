# mAP SSH access from RockPis

Each RockPi generates an ed25519 key at prepare time for passwordless SSH to the
MikroTik mAP (`172.27.213.1`). The setup wizard uses this to scan upstream SSIDs
and apply the station profile (no captive portal on the mAP).

## Per-node RouterOS users

Do **not** use `admin` for the nodes. Create one user per RockPi and attach that
board’s public key:

| Board | Reserved IP | mAP user (default) |
|---|---|---|
| RockPi 1 | `.195` | `rockpi1` |
| RockPi 2 | `.196` | `rockpi2` |
| RockPi 3 | `.194` | `rockpi3` |

The management service derives the username from `COT_R_DEVICE_ID` / reserved IP
unless you set `COT_R_MAP_USER` in `/etc/cot-r/mgmt.env` or `device.env`.

## On each RockPi

```bash
sudo ./scripts/cot-r prepare
sudo cat /etc/cot-r/ssh/id_ed25519.pub
```

Paths:

| File | Mode | Purpose |
|---|---|---|
| `/etc/cot-r/ssh/id_ed25519` | `0600` owner `cot-r` | Private key (mgmt service must read it) |
| `/etc/cot-r/ssh/id_ed25519.pub` | `0640` | Install on the mAP for **this** board’s user |

If scans fail with `Load key ... Permission denied`, fix ownership:

```bash
sudo chown cot-r:cot-r /etc/cot-r/ssh/id_ed25519
sudo chmod 0600 /etc/cot-r/ssh/id_ed25519
sudo systemctl restart cot-r-mgmt
```

## On the mAP (RouterOS 7)

Example for RockPi 2:

```routeros
/user add name=rockpi2 group=full password="..."
/user/ssh-keys/add user=rockpi2 key="ssh-ed25519 AAAA... cot-r@rockpi-2"
```

Repeat for `rockpi1` / `rockpi3` with each board’s own pubkey.

Verify from RockPi 2:

```bash
sudo -u cot-r ssh -i /etc/cot-r/ssh/id_ed25519 -o BatchMode=yes \
  rockpi2@172.27.213.1 '/system identity print'
```

Wireless scan (ROS7) used by the setup wizard:

```bash
sudo -u cot-r ssh -i /etc/cot-r/ssh/id_ed25519 -o BatchMode=yes \
  rockpi2@172.27.213.1 \
  ':foreach i in=[/interface wireless scan wlan1 duration=5 as-value] do={:put $i}'
```

Do not chain `scan …; scan print detail without-paging` (RouterOS replies
`expected end of command`).

Then restart management so it picks up the correct user:

```bash
sudo systemctl restart cot-r-mgmt
```

## Environment overrides

In `/etc/cot-r/mgmt.env` if needed:

```bash
COT_R_MAP_HOST=172.27.213.1
# COT_R_MAP_USER=rockpi2   # only if you did not use the defaults above
COT_R_MAP_WLAN=wlan1
COT_R_MAP_SEC_PROFILE=uplink
```

The security profile name `uplink` matches the existing station profile documented in
[networking.md](networking.md).
