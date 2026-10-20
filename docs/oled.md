# OLED display (SSD1306 / SH1106)

## Hardware

| Item | Detail |
|---|---|
| Panel | 0.96" 128×64 monochrome (SSD1306 or SH1106) |
| Header pin 1 | +3.3 V |
| Header pin 3 | I²C SDA |
| Header pin 5 | I²C SCL |
| Header pin 6 | Ground |

These are **physical RockPi header pin numbers**, not Linux GPIO numbers.

Match the **module silk labels** (GND / VCC / SCL / SDA), not “same ribbon orientation as
another board”. Cheap 4-pin OLEDs often use different left-to-right orders. ELEGOO
EL-SM-056 is typically labelled for GND–VCC–SCL–SDA; older modules may differ.

If RockPi A + OLED from board B works, the SoC/I²C7 path is fine. If only the new
panel fails on that same RockPi, suspect the panel pin order, a dead module, or a
probe false-negative (see below), not a different RockPi.

Shop listings say “SSD1306” often; silicon may still be **SH1106**. Both use address
`0x3c` (or `0x3d`). `prepare` / `oled-test` try both drivers; `COT_R_DISPLAY_DRIVER`
in `/etc/cot-r/display.env` records which one worked.

## RockPi 4B / RK3399 mapping

On Radxa Rock Pi 4B, header pins 3/5 map to **I2C7**:

| Signal | SoC pad | Armbian overlay |
|---|---|---|
| SDA | GPIO2_A7 | `rk3399-i2c7` |
| SCL | GPIO2_B0 | `rk3399-i2c7` |

Verified on this Armbian image:

- Base DTB `rk3399-rock-pi-4b.dtb` has `i2c@ff160000` (alias `i2c7`) with `status = "disabled"`.
- Overlay file: `/boot/dtb-*/rockchip/overlay/rockchip-rk3399-i2c7.dtbo`
- With `overlay_prefix=rockchip`, set in `/boot/armbianEnv.txt`:

```text
overlays=rk3399-i2c7
```

Do not confuse with `rk3399-i2c8` (different pins). Do not copy Raspberry Pi `dtparam=i2c_arm=on` instructions.

## Enabling and discovery (preparation)

`cot-r prepare` / OLED helpers:

1. Inspect existing `/boot/armbianEnv.txt` overlays (idempotent; no duplicate entries).
2. Add `rk3399-i2c7` if missing; back up the file first.
3. Report whether a **reboot** is required for the overlay to take effect.
4. After the bus appears, discover `/dev/i2c-*` and probe for an OLED ACK at `0x3c` or `0x3d` via `i2cdetect` (not a fragile register `i2cget`).
5. Install udev rules so the display service user can access the bus without running as root.
6. Run a short init/render self-test (`ssd1306`, then `sh1106` if needed).

Missing or disconnected OLED must not take down networking or recovery services.

### If prepare says “no OLED ACK”

On the board, with the suspect panel connected:

```bash
sudo i2cdetect -y 7
sudo ./scripts/cot-r oled-test
```

| `i2cdetect` | Meaning |
|---|---|
| `3c` or `3d` present | Bus OK; re-run prepare / oled-test (driver auto-try). |
| Only `--` in that row | No ACK: wrong module pin silk mapping, bad panel, or power. |

Swap test: RockPi under test + a known-good OLED. If that works, fix the new panel wiring or replace the module (3-pack: try another unit).

## Display service

Only **one** service owns the OLED device: `cot-r-display`.

- Default local API: Unix socket `/run/cot-r/display.sock`
- Optional LAN HTTP endpoint (later) must be authenticated
- Message priorities and a bounded queue coordinate competing clients
- Reset/error screens override ordinary animations
- Limits apply to image size, frame rate, animation duration, and uploads

Capabilities (incremental):

- Text lines with layout/alignment
- Basic geometric shapes
- Monochrome bitmaps and plots
- Status animations
- QR-code rendering
- Optional animated GIF → monochrome frames

Debian packages preferred on Armbian trixie: `python3-luma.oled`, `python3-luma.core`,
`python3-pil`, `python3-smbus2`, `python3-qrcode`, `i2c-tools`.

## QR-code constraints

A 128×64 panel severely limits scannable QR capacity. Modules must use an **integer**
pixel size; stretching to fill 64px would blur the code and usually hurts scanning.

Phone cameras join Wi-Fi from **QR** (`WIFI:…`) only. Data Matrix / Micro QR / Aztec are
not useful for one-tap join.

Default credentials are sized for this panel: SSID `Cardano-Rocks 1|2|3` and a **10-char**
PSK → about **31 modules** → **2px** on 64px height. Older long SSIDs (`Cardano-on-the-Rocks .195`)
need ~35 modules and only fit at 1px (unreliable).

Apply on existing boards:

```bash
sudo ./scripts/cot-r ap-prepare --rotate-psk
sudo ./scripts/cot-r ap-show-psk
```
