# RockPi AP bridging (next stage)

## Goal

Each unconfigured RockPi advertises its own compact SSID. Prefer **bridging** the AP
to Ethernet so phones get DHCP from the mAP (`172.27.213.1`) and the RockPi keeps its
reserved management address on the bridge.

Do **not** silently switch to a routed/NATed AP design.

## Safety

Changing Wi-Fi or creating bridges can disrupt access if done wrong. Preparation must:

- Preserve Ethernet/`end0` and SSH
- Never restart networking unexpectedly during baseline `prepare`
- Apply AP changes only via an explicit command (planned: `cot-r ap-enable`) after
  dry-run inspection
- Keep the existing DHCP reservation MAC identity

## Observed hardware (RockPi 2)

- Interface: `wlan0`
- Driver: `brcmfmac` (BCM4329-compatible SDIO)
- Default state during foundation work: DOWN

AP support must be verified with `iw phy` / driver capabilities on each board before
enabling hostapd.

## Planned layout

```text
  br0  (bridge)
   ├── end0   (Ethernet to mAP LAN)
   └── wlan0  (AP mode, hostapd)

  Management IPv4 stays on br0 (same reserved address via DHCP or static match).
  No DHCP server on the RockPi bridge.
```

Coordinator keeps AP up. Peers disable AP only after acknowledging a valid coordinator.

## Packages (when enabling)

Likely: `hostapd`, `bridge-utils` (or `iproute2` bridge), optionally `iw`.
Install only when the AP stage is activated, not as speculative baseline bulk.

## mAP interaction

Disable the mAP virtual AP when ready (inspect first):

```routeros
/interface wireless disable wlan-cardano
```

Preserve LAN bridge, reservations, NAT, upstream DHCP, and PoE settings.
