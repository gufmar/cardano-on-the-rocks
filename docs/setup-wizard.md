# Local setup wizard

Phone-driven configuration on the RockPi that served the Wi‑Fi (no mAP captive portal).

## Flow

1. OLED step **1**: scan Wi‑Fi QR → join `Cardano-Rocks N`
2. OLED step **2**: scan setup QR → open `http://<node>:8080/setup`
3. Web: confirm **“You are about to configure Cardano on the Rocks”**
4. OLED step **3**: gear icon (configuring). Optional `ctr by X` when adopted.
5. Web: check Internet; if needed, SSH to mAP → scan SSIDs → connect (or skip with warning)
6. Web: discover neighbours still on steps 1–2 → adopt into this wizard

Return to the uplink page anytime from the done screen.

## Apply

```bash
sudo ./scripts/cot-r prepare          # SSH keys + install static/setup.html
sudo ./scripts/cot-r ap-prepare       # if AP already on
sudo systemctl restart cot-r-display cot-r-mgmt cot-r-hostapd
```

Install each board’s pubkey on the mAP: [map-ssh.md](map-ssh.md).

## APIs (phone /setup)

| Method | Path | Auth |
|---|---|---|
| GET | `/setup` | none (HTML) |
| GET | `/api/v1/wizard/status` | none |
| GET | `/api/v1/wizard/internet` | none |
| POST | `/api/v1/wizard/confirm` | none → returns `session_id` |
| POST | `/api/v1/wizard/uplink/scan` | `X-Setup-Session` |
| POST | `/api/v1/wizard/uplink/connect` | session |
| POST | `/api/v1/wizard/uplink/skip` | session |
| GET | `/api/v1/wizard/peers` | session |
| POST | `/api/v1/wizard/adopt` | session |
| POST | `/api/v1/peer/wizard/adopt` | peer Bearer |

## Notes

- Gateway is `172.27.213.1` (not `.312`).
- Skip Internet is allowed but warned: later steps will likely fail.
- Adoption uses peer token + marks neighbours OLED step 3 with `ctr by <digit>`.
- Upstream Wi‑Fi passwords entered during uplink are stored only on the RockPi in
  `/etc/cot-r/uplink-credentials.json` (mode `0600`). The setup UI never receives
  cleartext; it only learns `psk_known` / `ready` so a saved SSID can be connected
  with one tap (or “Change password” to replace it). The live key on the mAP stays
  in security profile `uplink`.
