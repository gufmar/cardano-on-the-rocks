# OLED onboarding (skip mAP captive portal)

Instead of a guest captive page on the MikroTik, each RockPi guides the phone
locally with two OLED screens.

## Step 1 – join this board’s Wi‑Fi

```text
┌────────┬────────┐
│ 1      │        │
│ connect│  WIFI  │
│ WiFi   │   QR   │
└────────┴────────┘
```

- Big **1** (top left)
- Caption `connect` / `WiFi`
- Full-height Wi‑Fi QR on the **right** (`WIFI:T:WPA;S:Cardano-Rocks N;P:…`)

## Step 2 – open local setup

When **any** station associates to this AP (`hostapd_cli list_sta`), the screen
switches and stays on step 2 until demo reset:

```text
┌────────┬────────┐
│        │ 2      │
│ setup  │ setup  │
│  URL   │ node   │
│   QR   │        │
└────────┴────────┘
```

- Full-height setup QR on the **left** → `http://<mgmt-ip>:8080/setup`
- Big **2** (top right)
- Caption `setup` / `node`

`/setup` is a small unauthenticated HTML stub today; the full local UI comes later.

## Requirements

1. AP enabled (`ap-prepare` / `ap-enable`)
2. hostapd `ctrl_interface` + `ctrl_interface_group=cot-r` (written by ap-prepare)
3. `cot-r-mgmt` running (polls stations about every 2s)
4. Short SSID + 10-char PSK so the half-panel QR stays ≥2px

After changing hostapd config:

```bash
sudo ./scripts/cot-r ap-prepare
sudo systemctl restart cot-r-hostapd cot-r-mgmt cot-r-display
```

Waiting peers (stage 8) stop hostapd and show “Waiting for setup…” instead.

## Why this replaces a mAP captive portal

The phone already has L2 connectivity on the bridged LAN after scanning step 1.
Step 2 points at **this** RockPi’s management address, so association and setup
stay on the board the user physically looked at, without hijacking HTTP on the
router.
