# Amaru catch-up tuning (RockPi 4 GiB)

Catch-up is CPU-heavy by design (ledger/rules). Two settings on this board make
it worse than necessary.

## What we measured

| Signal | Observation |
|---|---|
| Progress | ~320k → ~136k blocks behind over ~5 h (~67 blocks/min) |
| Journal | ~20k JSON lines/min; mostly span `enter`/`exit`/`close` |
| Default log | `info,amaru::consensus=debug,amaru::ledger=debug,pure_stage=warn` |
| Memory | upstream `MemoryHigh=1G`; cgroup `memory.events high` ≫ 100k |

So DEBUG consensus/ledger + `AMARU_WITH_JSON_TRACES=true` floods journald, and
the 1 GiB high-water mark reclaim-throttles the node while all cores stay busy.

## Recommended catch-up profile

Apply via helper (restarts Amaru):

```bash
sudo /opt/cot-r/bin/cot-r-amaru-helper tune-catchup
```

Or set in `/etc/default/amaru` and restart:

```bash
AMARU_LOG=info
AMARU_TUI_OFF=true
AMARU_WITH_JSON_TRACES=true
```

Plus systemd drop-in `/etc/systemd/system/amaru.service.d/50-cot-r-catchup.conf`:

```ini
[Service]
MemoryHigh=2G
```

Then `systemctl daemon-reload && systemctl restart amaru`.

`AMARU_LOG=info` drops the default consensus/ledger DEBUG span flood. Keep JSON
traces on so wizard **Amaru observe** can still read `tip.adopt` /
`chainsync.roll_forward_done`.

## Optional next knobs

| Knob | Notes |
|---|---|
| `AMARU_PEERS_MAX_UPSTREAM=6` | More parallel chain sync; costs RAM/sockets |
| `AMARU_LOG=warn` | Even quieter; observe UI gets thinner |
| Do **not** set `AMARU_WITH_OPEN_TELEMETRY` | Extra export work on a small board |

Logging alone will not turn a 4 GiB aarch64 board into a fast relay; it should
free CPU/IO that was spent formatting and shipping DEBUG spans and fighting
memory reclaim.
