# Local setup wizard

Phone-driven configuration on the RockPi that served the Wi‑Fi (no mAP captive portal).

## Flow

1. OLED step **1**: scan Wi‑Fi QR → join `Cardano-Rocks N`
2. OLED step **2**: scan setup QR → open `http://<node>:8080/setup`
3. Web: confirm **“You are about to configure Cardano on the Rocks”**
4. OLED step **3**: gear icon (configuring). Optional `controlled by node X` when adopted.
5. Web: check Internet; if needed, SSH to mAP → scan SSIDs → connect (or skip with warning)
6. Web: discover neighbours still on steps 1–2 → adopt into this wizard
7. Web: **Nodes** (Amaru first)
    - **Board selector** — This board vs each adopted peer; status chips and actions follow the selection
    - **3.1 Install** — local: fetch/install `.deb` on this controller only; peer: LAN-install from master cache
    - **3.2 Bootstrap** — Mithril on **this board only** (~10 GiB); peers should **Copy DB** instead
    - **3.3 Config** — Start/Stop/Wipe for selected board; env editor + observe stay local-only
    - **Copy DB** — with peer selected: that peer only; with This board: all adopted peers.
      Streams stopped Amaru DB folders over LAN (no on-disk tarball). Peer needs roughly
      **1.15× DB size** free under `/var/lib/amaru`. Old `*.tgz` cache files are cleared
      on push. UI shows job phases (size / install / stream).

After start success, observation stays inside the wizard HTTP UI; see
[amaru-operator-ui.md](amaru-operator-ui.md).

Use the top-right step menu to jump between Start, Internet uplink, Neighbour
nodes, and Nodes (after confirm).

## Presentation prep

Lab with Internet: run 3.1 + 3.2 on the controller, then **Copy snapshot to adopted**.
At the venue, uplink + adopt still run; Nodes shows installed/bootstrapped status.
**Auto-start** defaults off so Amaru starts only when you click Start in 3.3.

Demo reset does **not** wipe Amaru databases. Use **Wipe DB** explicitly.

## Apply

```bash
sudo ./scripts/cot-r prepare          # helper + sudoers + setup.html
sudo ./scripts/cot-r ap-prepare       # if AP already on
sudo systemctl restart cot-r-display cot-r-mgmt cot-r-hostapd
```

Install each board’s pubkey on the mAP: [map-ssh.md](map-ssh.md).

Amaru upstream guide: https://amaru.global/user-guide/mainnet/

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
| GET | `/api/v1/wizard/peers` | session (available / adopted / busy) |
| POST | `/api/v1/wizard/adopt` | session `{addrs:[…]}` |
| POST | `/api/v1/wizard/free` | session `{addr}` |
| GET | `/api/v1/wizard/node/status` | session |
| POST | `/api/v1/wizard/node/install` | session |
| POST | `/api/v1/wizard/node/bootstrap` | session |
| POST | `/api/v1/wizard/node/bootstrap/stop` | session (stop Mithril / oneshot) |
| GET/PUT | `/api/v1/wizard/node/env` | session |
| POST | `/api/v1/wizard/node/service` | session `{action}` |
| POST | `/api/v1/wizard/node/autostart` | session `{enabled}` |
| POST | `/api/v1/wizard/node/wipe` | session |
| POST | `/api/v1/wizard/node/push-snapshot` | session |

Peer (Bearer `peer.token`): `/api/v1/peer/node/status`, `install`, `snapshot/bundle`, `snapshot/pull`, `service`, `autostart`, `wipe`.

## Notes

- Env file path is `/etc/default/amaru` (Debian package).
- Privileged ops go through `/opt/cot-r/bin/cot-r-amaru-helper` (sudoers).
- Upstream Wi‑Fi passwords stay in `/etc/cot-r/uplink-credentials.json` (mode `0600`).
- `POST /api/v1/reset` with `{"scope":"demo"}` releases peers and clears wizard state; Amaru DBs remain.
