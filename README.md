# reSpeaker Clip Firmware

Zephyr RTOS firmware for the **Seeed reSpeaker Clip** — a wearable voice
recording device based on the Nordic nRF5340 dual-core MCU, with BLE, WiFi AP,
USB, AT-command control, and UDP file transfer.

> **Note**: Product name is spelled **reSpeaker** (lowercase `r`).

## Hardware

| Component | Part |
|-----------|------|
| MCU | nRF5340 (Application core + Network core, dual-core) |
| WiFi | nRF7002 (QSPI, AP mode) |
| PMIC / Charger | NPM1300 + nRF Fuel Gauge |
| Display | CH1115 OLED (88×48) |
| Audio | PDM microphone array (DMIC) |
| Storage | microSD (FAT) + 64 Mbit external SPI flash (LittleFS) |
| Connectivity | BLE 5.x + WiFi 2.4/5G AP + USB CDC ACM + USB MSC |

## Key Features

- **Audio**: PDM mic → SpeexDSP preprocessing (noise suppression / AGC / dereverb) → Opus encoding
- **BLE**: AT-command protocol, OTA DFU (MCUmgr), GATT notifications, RTC live audio streaming (`AT+START=RTC`)
- **WiFi**: AP mode (`ClipAP_XXXX`) with UDP file transfer (CRC32-verified)
- **USB**: CDC ACM serial (3rd AT channel) + MSC mass storage (SD card) + 1200-baud → DFU recovery trigger
- **Power**: Production idle ~170µA (DCDC, SD power-gating, console off)
- **Battery**: NPM1300 charging + nRF Fuel Gauge SoC, custom "240" cell model
- **OTA**: MCUboot (custom) with signed images, BLE/USB serial DFU

## Getting Started

### Prerequisites

- [nRF Connect SDK (NCS) v3.3.0](https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/index.html)
- Zephyr SDK (toolchain)
- `west` (Zephyr's meta-tool)
- nRF Connect for Desktop (flashing) or `nrfutil`
- Python 3.10+ (for test tools)

### Build

```sh
# 1. Source the NCS v3.3.0 environment
source ~/ncs/v3.3.0/zephyr/zephyr-env.sh

# 2. Set the module path (REQUIRED — enables Kconfig to discover this repo's
#    board/drivers/lib. Must be an env var, not -D, because Kconfig module
#    discovery runs before CMake.)
export ZEPHYR_EXTRA_MODULES=$(pwd)

# 3. Build the clip app (sysbuild: mcuboot + app + network-core radio)
west build --build-dir build-clip --board clip/nrf5340/cpuapp applications/clip
```

**Production (low-power, console off):**
```sh
west build --build-dir build-clip-prod --board clip/nrf5340/cpuapp applications/clip \
  -- -DSNIPPET_ROOT=$(pwd)/applications/clip -DSNIPPET=production
```

**Development (console over USB CDC — no J-Link / UART adapter needed):**
```sh
west build --build-dir build-clip-dev --board clip/nrf5340/cpuapp applications/clip \
  -- -DFILE_SUFFIX=dev
```
This variant auto-enables the app's USB CDC ACM at boot (`CONFIG_CLIP_USB_AUTO_ENABLE=y`)
and redirects the console + UART log backend from uart0 to the CDC port, so logs are
visible over USB the moment the device is plugged in. It uses **dynamic MSC handoff**
(`CONFIG_CLIP_USB_MSC_DYNAMIC=y`): the USB device (CDC + MSC) stays enumerated at all
times, and the SD card is handed between host and app by ejecting/inserting the MSC
*media* — never by disabling USB. Flash it like any other app image
(signed bin via USB DFU, or `merged.hex` with a J-Link). Notes:

- Log level follows the app's `prj.conf` (`CONFIG_CLIP_LOG_LEVEL`) — same as the debug build.
- **Recording/transfer never blocked by USB:** when one starts, the MSC media is
  reported ejected (host sees "no media") and the card is mounted in the app; when it
  stops, the card is unmounted and the media comes back. The CDC serial port never
  disappears, so console logs keep flowing through the whole recording.
- The FS (SD) log backend is **disabled** in this variant (`CONFIG_LOG_BACKEND_FS=n`)
  to fit the fixed app partition; `AT+LOG=info`/`debug` answers "FS log backend
  unavailable". Logs live on the CDC console only.
- Messages printed before USB enumeration are lost; with no host attached, console output
  is dropped (never blocks boot).
- `FILE_SUFFIX` files are discovered only at configure time: if you add/remove suffixed
  files, rebuild with `--pristine` (a stale `DTC_OVERLAY_FILE` is cached otherwise).

**Host-side conflicts (read before using this mode):**

- **ModemManager** probes new CDC-ACM ports and can grab the Clip's port (AT
  replies get eaten; `minicom`/scripts see a busy or garbled port). Ignore the
  Clip by VID:
  ```sh
  sudo tee /etc/udev/rules.d/98-clip-mm-ignore.rules <<'EOF'
  SUBSYSTEM=="usb", ATTRS{idVendor}=="2886", ENV{ID_MM_DEVICE_IGNORE}="1"
  SUBSYSTEM=="tty", ATTRS{idVendor}=="2886", ENV{ID_MM_DEVICE_IGNORE}="1"
  EOF
  sudo udevadm control --reload-rules
  ```
- **Desktop storage daemons** (udisks2/automount) probe the SD card over MSC on
  every plug, and again whenever the MSC media reappears (after a recording or
  transfer ends). The probing interrupts the device and can disturb an active
  session; during development stop udisks2 (`sudo systemctl stop udisks2`) or
  add an ignore rule for the Clip's block device.
- **Keep the CDC port free while flashing.** If a terminal (`minicom`,
  `screen`, a leftover `clip.terminal`) holds `/dev/ttyACMx`, mcumgr DFU
  uploads time out. Close every console on the port before entering DFU.
- **MSC vs. recording (dynamic handoff, this build):** recording is never
  refused — starting one ejects the MSC media and takes the card back. If the
  host still had the card *mounted*, that mount hits I/O errors (same as
  physically pulling the card); eject/unmount on the host first when possible.
  After the recording stops the media reappears; file managers may need a
  refresh/re-mount to see it again. Product builds use the static handoff
  instead: enabling USB refuses while recording/transfer is active and vice
  versa.

**Logs during normal recording:** yes, unconditionally — the CDC console and
the recording coexist by design. Logs keep flowing over CDC for the whole
recording whether the cable was plugged before or during it (USB enable is
never refused; the MSC media simply stays ejected while the app owns the
card).

**When USB is enabled / disabled (dev mode):**

| Event | Action |
|---|---|
| Boot with cable plugged | CDC+MSC auto-enabled after enumeration (`CONFIG_CLIP_USB_AUTO_ENABLE`) |
| Recording/transfer starts while USB up | MSC media ejected (host sees "no media"), card handed to the app, CDC untouched |
| Recording/transfer stops while USB up | Card unmounted from the app, MSC media present again |
| `AT+USB=ON` (any build) | Enabled; if no VBUS is detected a 10-min idle timer starts (`USB_NO_VBUS_TIMEOUT_MS`) |
| VBUS droop while active (dev builds) | 3 s grace (`USB_VBUS_LOST_GRACE_MS`, `CONFIG_CLIP_USB_AUTO_ENABLE`) rides through load spikes (radio TX bursts, mic/DSP start); still gone afterwards -> disabled + `"usb":"off"` event. Product builds disable immediately on VBUS loss. |
| VBUS returns while inactive (dev builds) | Auto re-enables the console (no reboot, no `AT+USB=ON` needed) |
| No VBUS for 10 min | Auto-disabled |
| `AT+USB=OFF` | Disabled manually |
| After DFU `serial reset` | CDC re-enumerates automatically; requires the `udc_nrf` VBUS patch, see `patches/zephyr/` |

> **Board identifier**: `clip/nrf5340/cpuapp` (NOT `respeaker/...`)

### Firmware Upgrade (USB — no J-Link needed)

The reSpeaker Clip ships in an **enclosed housing**, so the SWD/J-Link pads are
not reachable for end users. Firmware upgrades happen over **USB** (or BLE) with
mcumgr — no probe, no opening the case. Every clip app has the **1200-baud DFU
trigger** built in (board-level, `lib/clip_usb_dfu`).

1. Enter MCUboot serial recovery — open the device's USB CDC-ACM port at
   **1200 baud** (the app reboots into recovery automatically):
   ```sh
   python3 -c "import serial; s=serial.Serial('/dev/ttyACMx',1200); s.close()"
   ```
   The clip app keeps USB off by default — send `AT+USB=on` over BLE first.
   Samples and custom apps with the default CDC auto-enable USB (no BLE step).
   (Holding the user button while plugging USB also enters recovery.)
2. A new CDC-ACM port appears — **PID `0x8069`** (the running app is `0x0069`;
   the `0x8000` bit marks bootloader mode; both Seeed VID `0x2886`). Upload the
   signed app:
   ```sh
   nrfutil mcu-manager serial image-upload --firmware clip-<v>-signed.bin --serial-port /dev/ttyACMx
   nrfutil mcu-manager serial reset     --serial-port /dev/ttyACMx
   ```
   MCUboot verifies the signature and boots the new app; the bootloader partition
   is never touched.

Full guide (BLE OTA, the button path, `mcumgr`/nRF Connect, troubleshooting):
[docs/usb_dfu.md](docs/usb_dfu.md).

### Flash (development — J-Link/SWD)

For development with a debug probe. The enclosed device has no user-accessible SWD
— end users use USB DFU (above).

```sh
# west flash handles the dual-core routing (app + net core)
west flash --build-dir build-clip && nrfutil device reset
```

> `west flash --reset` does NOT work on this board — use `nrfutil device reset`
> after flashing. If the net-core access port is b0n-locked (after a prior boot),
> add `--recover`.

### Serial Console

```sh
minicom -D /dev/ttyACM0 -b 921600
```

## Project Structure

| Path | Description |
|------|-------------|
| `applications/clip/` | Main application (AT commands, audio, BLE, WiFi, storage) |
| `boards/seeed/clip/` | Board support package (device trees, Kconfig) |
| `drivers/` | Custom drivers (GPIO button) |
| `lib/` | Libraries (Opus, SpeexDSP, Lua, 1200-baud USB DFU trigger) |
| `samples/` | Example apps (hello_world, opus_encode, wifi_ap_iperf, etc.) |
| `tests/` | Factory/RF test firmware (`clip`, `otp`, `dtm`, `wifi_radio`, `re`, ...) |
| `patches/mcuboot/` | MCUboot customization patches (applied to the NCS tree) |
| `docs/` | Project documentation |

## Documentation

### Official References

- **[nRF Connect SDK](https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/index.html)** — NCS documentation (this firmware targets NCS v3.3.0)
- **[Zephyr Project](https://docs.zephyrproject.org/)** — Zephyr RTOS documentation
- **[nRF5340 Product Page](https://www.nordicsemi.com/Products/nRF5340)** — MCU datasheet & specs
- **[nRF7002](https://www.nordicsemi.com/Products/nRF7002)** — WiFi chipset
- **[NPM1300](https://www.nordicsemi.com/Products/npm1300)** — PMIC / battery charger

### Project Docs (`docs/`)

| Doc | Description |
|-----|-------------|
| [architecture.md](docs/architecture.md) | System architecture & design |
| [protocol.md](docs/protocol.md) | BLE AT command protocol specification |
| [udp_protocol.md](docs/udp_protocol.md) | WiFi UDP file transfer protocol |
| [requirements.md](docs/requirements.md) | Product requirements |
| [custom_app_guide.md](docs/custom_app_guide.md) | **Custom app development guide** — build, flash, BLE OTA, USB serial DFU recovery |
| [usb_dfu.md](docs/usb_dfu.md) | Firmware upgrade guide (USB / BLE / programmer) |
| [audio_quality_standard.md](docs/audio_quality_standard.md) | Audio recording quality standard |
| [development.md](docs/development.md) | Development log |
| [whitepaper.md](docs/whitepaper.md) | Firmware whitepaper |

See [CLAUDE.md](CLAUDE.md) for detailed build/flash/power-management guidance
and known pitfalls.

## Testing

```sh
# BLE protocol tests
python tests/ble_test.py --interactive

# WiFi UDP file sync (connect to ClipAP_XXXX first; password 12345678 by default,
# becomes a random one after the first BLE pairing)
python applications/clip/tests/tools/udp_sync.py --session <session_id>

# Hardware test firmware
west build --build-dir build-test --board clip/nrf5340/cpuapp --pristine tests/clip
```

WiFi AP: SSID `ClipAP_XXXX` (last 4 hex of chip ID) · Password `12345678` (default; random after first pairing) · IP `192.168.4.1` · UDP Port `8089`

## Mobile App & SDK

The companion phone app and SDKs (Flutter, Android, iOS) live under
[`mobile/`](mobile/README.md). They talk to the Clip over BLE and the device
Wi-Fi AP — no API key or backend required. See the mobile monorepo README for
the layout, running the example/sample apps, and the integration & verification
guides in `mobile/docs/`. **The mobile SDKs are separately licensed** (see each
`mobile/sdk/*/LICENSE`) and are not covered by the repository Apache-2.0
license below.

## License

This firmware is licensed under the [Apache License 2.0](LICENSE). See
individual files for `SPDX-License-Identifier` details. Third-party libraries
(Opus, SpeexDSP, Lua) retain their respective licenses. The `mobile/` SDKs are
separately licensed (see above).

## Acknowledgements

- [Nordic Semiconductor](https://www.nordicsemi.com/) — nRF Connect SDK, nRF5340, nRF7002, NPM1300
- [Zephyr Project](https://zephyrproject.org/) — RTOS
- [Seeed Studio](https://www.seeedstudio.com/) — reSpeaker Clip hardware
