# Amaru operator UI (after node start)

Our setup wizard covers install, Mithril bootstrap, env, and Start/Stop.
Once `amaru.service` is active, cot-r shows a **bounded observation UI** in
wizard step **3.3** (Overview / Sync / Peers / Host).

## How observation works

```text
Amaru JSON traces -> journald -> cot-r-amaru-helper status -> wizard panels
```

The helper rebuilds a small live projection from Amaru’s public journal
fields (tip / current / peer / tip.adopt / …) plus `/proc` host samples.
It does **not** open Amaru’s databases or invent missing telemetry.

Panels:

| Panel | Content |
|---|---|
| Overview | Phase, local tip, network tip, gap, sync bar |
| Sync | Slot/block gaps, event counts, recent interesting events |
| Peers | Addresses seen in recent journals + last message |
| Host | Load averages, memory |

Polling continues while `amaru.service` is active or activating.

## Inspiration (not a dependency)

[KtorZ/amaru-a7z-bluetooth-ios-bridge](https://github.com/KtorZ/amaru-a7z-bluetooth-ios-bridge)
uses the same journal → projection idea, then ships over **Bluetooth LE** to an
iOS/Tauri app. That transport does not fit our AP phone flow. We keep the
observability approach and render over the existing HTTP setup wizard instead
of merging the BLE stack.

## Tip fields (do not mix)

| Source | Meaning |
|---|---|
| `tip.adopt` / `tip.update` (`slot`, `block_height`) | Local **adopted** tip |
| `tip.adopt.max_block_height` | Local header pipeline ahead of adopted |
| `chain.tip_accepted.tip` | Local chain tip (not peer network tip) |
| `chainsync.roll_forward_done` `tip` / `highest` / `tip_height` | Peer-advertised **network** tip |

Mixing the last row with `max_block_height` made the UI oscillate between
“~1k behind” and “~330k behind” even while Amaru advanced steadily.

## Limits

- Projection is windowed (`journalctl -n 800`); quiet peers drop out of the list.
- Network tip is peer-advertised in traces, not a separate API; we keep a
  high-water mark inside the window.
- Schema drift in Amaru traces may require helper tweaks; prefer public
  field names over private spans.
