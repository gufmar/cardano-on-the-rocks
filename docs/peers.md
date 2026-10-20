# Peer coordination

Stage 8 foundation: discover reserved RockPis over Ethernet, elect one
coordinator for a demo session, and put acknowledging peers into Waiting
(AP standby + OLED message).

## Trust model

| Secret | Path | Scope |
|---|---|---|
| Admin API token | `/etc/cot-r/api.token` | Per-board operator access (status, claim, reset) |
| Shared peer token | `/etc/cot-r/peer.token` | Identical on all boards; peer-to-peer only |

`prepare` creates both if missing. For a cluster, copy **one** `peer.token` to
every board (do not leave three different random values).

Never commit tokens. Never put peer or API tokens in general notification payloads.

Reserved addresses (`172.27.213.194` / `.195` / `.196`) are discovery hints only.
Authenticate with `device.id` + peer token; do not trust IP alone.

Optional override in `/etc/cot-r/device.env`:

```bash
COT_R_PEER_ADDRS=172.27.213.194,172.27.213.195,172.27.213.196
```

## Claim flow

1. Operator (or future setup UI) calls `POST /api/v1/setup/claim` with the **API** token
   on the board that should own the session.
2. That board becomes coordinator (`Configuring`), starts a session id and lease.
3. It POSTs `/api/v1/peer/claim` to the other reserved addresses with the **peer** token.
4. Peers that accept enter `Waiting`, stop `cot-r-hostapd` (Ethernet/bridge stays up),
   and show “Waiting for setup…” on the OLED.
5. Coordinator refreshes leases via `/api/v1/peer/heartbeat` about every 15s.
6. Missing peers do not block setup.
7. Lease expiry or `POST /api/v1/peer/release` returns a peer to `Initial` and resumes hostapd.

Split-brain: if two boards claim at once, the **lower** `device.id` (UUID string)
keeps coordinator; the other yields.

Ended sessions are recorded locally so a reset peer cannot resurrect an old claim.

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/peers` | API | Probe reserved peers (+ hello if peer token set) |
| POST | `/api/v1/setup/claim` | API | Become coordinator and announce |
| GET | `/api/v1/peer/hello` | peer | Identity / state exchange |
| POST | `/api/v1/peer/claim` | peer | Accept or reject claim |
| POST | `/api/v1/peer/heartbeat` | peer | Refresh Waiting lease |
| POST | `/api/v1/peer/release` | peer | Session ended → Initial |

`GET /api/v1/status` also reports `role`, `lease_expires_at`, and `peer_acks`.

## Privileged helper

Waiting peers stop/start hostapd via sudoers (`/etc/sudoers.d/cot-r-mgmt`):

```text
cot-r ALL=(root) NOPASSWD: /usr/bin/systemctl stop cot-r-hostapd.service
cot-r ALL=(root) NOPASSWD: /usr/bin/systemctl start cot-r-hostapd.service
```

Installed by `cot-r prepare`. Coordinator keeps its AP running.

## Prerequisite: shared peer token

Both boards must use the **same** `/etc/cot-r/peer.token`. After prepare on board A:

```bash
# On RockPi 2 (example): copy token to RockPi 1
sudo cat /etc/cot-r/peer.token
# On RockPi 1:
echo 'PASTE_SAME_TOKEN' | sudo tee /etc/cot-r/peer.token
sudo chown root:cot-r /etc/cot-r/peer.token
sudo chmod 0640 /etc/cot-r/peer.token
sudo systemctl restart cot-r-mgmt
```

API tokens (`api.token`) stay per-board and different.

## Manual smoke test (two boards)

On board A (the one that should become coordinator), after peer tokens match:

```bash
TOKEN=$(sudo cat /etc/cot-r/api.token)
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/v1/peers | python3 -m json.tool
# Expect the other board reachable with hello.state / device_id
curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' http://127.0.0.1:8080/api/v1/setup/claim | python3 -m json.tool
```

On board B:

```bash
TOKEN=$(sudo cat /etc/cot-r/api.token)
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/v1/status | python3 -m json.tool
# Expect: state=Waiting, role=peer, coordinator_id=<A>
systemctl is-active cot-r-hostapd   # expect inactive
# OLED: "Waiting for setup..."
```

Demo reset on the coordinator:

```bash
curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scope":"demo"}' http://127.0.0.1:8080/api/v1/reset | python3 -m json.tool
```

Peers should return to Initial and resume the AP when hostapd was prepared.

## Not in this stage

- Phone association → automatic claim (needs portal / setup UI)
- RouterOS uplink changes gated by coordinator
- Whole-demo reset UX beyond best-effort peer release on API reset
