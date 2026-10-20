# OLED display (SSD1306)

## Hardware

| Item | Detail |
|---|---|
| Panel | 0.96" SSD1306, 128×64 monochrome |
| Header pin 1 | +3.3 V |
| Header pin 3 | I²C SDA |
| Header pin 5 | I²C SCL |
| Header pin 6 | Ground |

These are **physical header pin numbers**, not Linux GPIO numbers.

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
4. After the bus appears, discover `/dev/i2c-*` and probe for SSD1306 (commonly `0x3c` or `0x3d`; **verify**, do not hard-assume).
5. Install udev rules so the display service user can access the bus without running as root.
6. Run a short init/render self-test.

Missing or disconnected OLED must not take down networking or recovery services.

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
