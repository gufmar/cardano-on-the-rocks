# RockPi AP bridging

## Goal

Each unconfigured RockPi advertises its own SSID derived from its LAN lease:

`Cardano-on-the-Rocks .<last-octet>`  
Example: address `172.27.213.196` → SSID `Cardano-on-the-Rocks .196`

Prefer **bridging** the AP to Ethernet so phones get DHCP from the mAP
(`172.27.213.1`) and the RockPi keeps its reserved management address on the bridge.

Do **not** silently switch to a routed/NATed AP design.

## Verified on RockPi 2

| Item | Value |
|---|---|
| Interface | `wlan0` |
| Driver | `brcmfmac` |
| PHY | `phy0` supports **AP** mode (`iw phy`) |
| Ethernet | `end0` MAC used for mAP DHCP reservation |
| Stack | systemd-networkd via Netplan |

## Layout

```text
  br0  (bridge, MAC = end0 permanent address)
   ├── end0   (Ethernet to mAP LAN; no DHCP client on end0 itself)
   └── wlan0  (AP mode; hostapd adds this to br0)

  Management IPv4 on br0 via DHCP from 172.27.213.1
  No DHCP server on the RockPi.
```

## Commands (explicit; not part of baseline `prepare`)

```bash
./scripts/cot-r ap-status                 # inspection only
sudo ./scripts/cot-r ap-show-psk          # SSID + PSK + ASCII Wi-Fi QR (root)
sudo ./scripts/cot-r ap-show-psk --no-qr  # text only
sudo ./scripts/cot-r ap-prepare           # packages, SSID/PSK, hostapd.conf (no bridge yet)
sudo ./scripts/cot-r ap-enable --confirm  # write netplan bridge + start hostapd
sudo ./scripts/cot-r ap-disable --confirm # tear down AP, restore Ethernet DHCP
```

`--confirm` is required for enable/disable because networking is reconfigured.
Expect a short SSH blip during `netplan apply`. Stay on Ethernet.

### Defaults on this board

| Setting | Default |
|---|---|
| SSID | `Cardano-on-the-Rocks .<last-octet>` (e.g. `Cardano-on-the-Rocks .196`) |
| Channel | `6` (2.4 GHz) |
| Country | `CH` (override in `/etc/cot-r/ap.env`) |
| PSK | `/etc/cot-r/ap.psk` (generated once; kept across demo resets) |

## Safety properties

- Baseline `prepare` never installs hostapd or writes AP netplan.
- Bridge MAC is set to the permanent `end0` address so reservations stay valid.
- AP PSK and `ap.env` survive demo reset and project reset by default.
- Project reset disables an active AP (removes netplan fragment) so Ethernet returns.
- No extra DHCP/NAT on the RockPi.

## Files

| Path | Role |
|---|---|
| `/etc/cot-r/ap.env` | Non-secret AP settings |
| `/etc/cot-r/ap.psk` | WPA2 passphrase (secret) |
| `/etc/cot-r/hostapd.conf` | Generated hostapd config |
| `/etc/netplan/60-cot-r-bridge.yaml` | Bridge fragment (only while enabled) |
| `/etc/systemd/system/cot-r-hostapd.service` | Project hostapd unit |
| `/var/lib/cot-r/ap-enabled` | Enable marker |

## mAP interaction

When RockPi APs take over onboarding, disable the mAP virtual AP (inspect first):

```routeros
/interface wireless disable wlan-cardano
```

Preserve LAN bridge, reservations, NAT, upstream DHCP, and PoE settings.

## OLED / QR note

The SSID is longer than a compact code name. Wi-Fi QR payloads on a 128×64 panel
are tight; keep the PSK compact (as generated) and always offer a readable
SSID/PSK/setup-URL fallback screen if the QR cannot encode reliably.

## Next after AP works

- Peer coordination (peers hide QR / disable AP after coordinator ack)
- Association reporting for portal handoff
- Local setup UI + Wi-Fi QR on OLED (with readable fallback)
