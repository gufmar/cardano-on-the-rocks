# OLED Nodes slides (wizard 3.x)

Once the phone reaches **Nodes** (wizard confirmed / step ≥ 3), the OLED leaves
the static gear screen and rotates a short carousel (~3.5 s per slide).

| Slide | Content |
|---|---|
| Progress | 3.1 / 3.2 / 3.3 phase + job or catch-up gap |
| CPU | Load 1/5/15, cpu/gpu °C |
| Memory | OS used / total / peak; Amaru RSS / peak |
| Tip | Isometric block with tip height; new tip slides in from the right |
| Mempool | Pending count when available; else last block `tx_count` from journals |

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
