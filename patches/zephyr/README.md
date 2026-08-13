# Zephyr Patches

Patches against the Zephyr tree in NCS (`~/ncs/v3.3.0/zephyr`, NCS v3.3.0).
Apply after a fresh NCS install (idempotent: `git apply` fails if already applied,
check with `git status` in the zephyr tree).

```sh
cd ~/ncs/v3.3.0/zephyr
git apply /path/to/reSpeaker_Clip/patches/zephyr/0001-udc-nrf-synthesize-vbus-events-when-already-present.patch
```

---

## 0001-udc-nrf-synthesize-vbus-events-when-already-present.patch

**File**: `drivers/usb/udc/udc_nrf.c` (`udc_nrf_init()`)

### Problem

USBREG power events (USBDETECTED/USBPWRRDY) are edge-triggered. After a **warm
reset out of mcuboot serial recovery** (`nrfutil mcu-manager serial reset`),
VBUS is still present, so no new event fires. The new USB stack driver
(`udc_nrf`, `CONFIG_USB_DEVICE_STACK_NEXT`) only arms those interrupts at init
and never polls the current state, so `nrf_usbd_legacy_start()` (interrupts +
pull-up) never runs and `UDC_EVT_VBUS_READY` is never submitted. The host sees
a zombie connection that fails enumeration:

```
usb 1-11: Device not responding to setup address.
usb 1-11: device not accepting address N, error -71
```

Only a full power cycle (PMIC reset / cable re-plug) recovered USB. The legacy
`usb_dc_nrfx` driver (used by mcuboot itself) has always handled this case by
polling `nrfx_power_usbstatus_get()` and synthesizing the event — which is why
mcuboot USB works after a warm reset from the app, but the app USB never worked
after a warm reset from mcuboot.

### Fix

Mirror the legacy driver: at `udc_nrf_init()` time, if the regulator already
reports VBUS present, synthesize `NRFX_POWER_USB_EVT_DETECTED` (and
`NRFX_POWER_USB_EVT_READY` when the regulator output is ready).

Paired with the app-side `usb_cdc.c` change that re-syncs `usb_vbus_present`
from `nrfx_power_usbstatus_get()` in `usb_cdc_enable()` (events synthesized
before the app registers its usbd message callback would otherwise be lost).
