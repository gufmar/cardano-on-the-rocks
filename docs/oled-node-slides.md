# OLED Nodes slides (wizard 3.x)

Once the phone reaches **Nodes** (wizard confirmed / step ≥ 3), the OLED leaves
the static gear screen and rotates a short carousel (~3.5 s per slide).

| Slide | Content |
|---|---|
| Logo | Amaru mark (1-bit from `logo-icon.svg`) + label |
| Progress | 3.1 / 3.2 / 3.3 phase; **On tip** when gap ≤ 300 blocks/slots |
| CPU | Per-core bars (border = 100%, fill = busy %) + SoC °C |
| Memory | `Mem OS` / `Mem Node` bars inside a full-RAM border |
| Tip | Small isometric block left; large tip height on the right |
| Mempool | Pending count when available; else last block `tx_count` |

## Tip tolerance

Gaps of **≤ 300** blocks (or slots from the helper) count as **on tip**. The OLED
shows `synced` / `ok ΔN` instead of alarming. Catch-up only appears when further
behind. Footer line uses `Amaru`, not a vague `Nodes` label.

## Logo asset

Prefer the compact **SVG icon** (`https://amaru.global/layout/logo-icon.svg`) over
the wide wordmark: on a 128×64 mono panel a simple mark thresholds cleanly.
Vendored as `display/cot_r_display/assets/amaru-logo-icon.svg` and pre-rasterized
`amaru-mark-48.{png,json}`.

## Plumbing

```text
cot-r-mgmt NodeOled  ->  display.sock cmd=node_slide  ->  cot-r-display renderer
```

- Priority **72** (above onboarding gear).
- Tip / gap from filtered `journalctl` (`tip.adopt`, `tip.update`,
  `chainsync.roll_forward_done`) plus helper `run` projection when present.
- Memory peaks are tracked since mgmt start (cleared on demo reset).
- Mempool pending is **n/a** until Amaru exposes it in traces/API; the slide
  still shows last adopted block TX count from `tip.update`.

Env knobs:

| Variable | Default | Meaning |
|---|---|---|
| `COT_R_NODE_OLED_SLIDE` | `3.5` | Seconds per slide |
| `COT_R_NODE_OLED_SAMPLE` | `2.0` | Idle wait when carousel inactive |
| `COT_R_NODE_OLED_ON_TIP_GAP` | `300` | Max gap still shown as on tip |
