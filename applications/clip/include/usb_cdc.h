/*
 * Copyright (c) 2025 Seeed Technology Co., Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef CLIP_USB_CDC_H
#define CLIP_USB_CDC_H

#include <zephyr/usb/usbd.h>

/**
 * @brief Initialize USB CDC ACM (virtual serial port for AT commands)
 *
 * Initializes the USB device with CDC ACM + MSC classes.
 * USB starts disabled; use usb_cdc_enable() to activate.
 *
 * @return 0 on success, negative error code on failure
 */
int usb_cdc_init(void);

/**
 * @brief Enable USB (CDC + MSC)
 *
 * Call after BLE AT+USB=on. Makes device visible to USB host.
 *
 * @return 0 on success, negative error code on failure
 */
int usb_cdc_enable(void);

/**
 * @brief Disable USB (CDC + MSC)
 *
 * Call after BLE AT+USB=off. Device disappears from USB host.
 *
 * @return 0 on success, negative error code on failure
 */
int usb_cdc_disable(void);

/**
 * @brief Check if USB is enabled
 *
 * @return true if USB is enabled and active
 */
bool usb_cdc_is_enabled(void);

/**
 * @name Dynamic MSC handoff (CONFIG_CLIP_USB_MSC_DYNAMIC)
 *
 * In dynamic builds (dev) the USB device stays enumerated and the SD card
 * is handed between host and app by ejecting/inserting the MSC media:
 * recording/transfer acquire the card, completion releases it back.
 *
 * In product (static) builds these collapse to the plain USB-enabled state
 * with zero runtime cost: the host owns the card exclusively while USB is
 * up and recording is refused in that time.
 * @{
 */
#if IS_ENABLED(CONFIG_CLIP_USB_MSC_DYNAMIC)

/**
 * Check if the USB host may currently access the SD card: only while USB
 * is enabled AND the MSC media is present (not ejected by an active
 * recording or transfer). Use for SD-rail/busy decisions.
 */
bool usb_msc_is_enabled(void);

/**
 * Check whether starting a recording must be refused because of USB.
 * Dynamic handoff ejects the MSC media instead and never blocks recording.
 */
bool usb_msc_blocks_recording(void);

/**
 * App requests exclusive access to the SD card. Called when a recording or
 * file transfer starts; with USB active the MSC media is reported ejected.
 * Must be paired with usb_msc_sd_release().
 */
void usb_msc_sd_acquire(void);

/**
 * App releases the SD card. Called when a recording or file transfer is
 * fully finished; with USB active the card is unmounted and the MSC media
 * is reported present again. Must be called once per successful acquire.
 */
void usb_msc_sd_release(void);

#else /* !CONFIG_CLIP_USB_MSC_DYNAMIC: static handoff (product behavior) */

static inline bool usb_msc_is_enabled(void)
{
	return usb_cdc_is_enabled();
}

static inline bool usb_msc_blocks_recording(void)
{
	return usb_cdc_is_enabled();
}

static inline void usb_msc_sd_acquire(void)
{
}

static inline void usb_msc_sd_release(void)
{
}

#endif /* CONFIG_CLIP_USB_MSC_DYNAMIC */
/** @} */

/**
 * @brief Get shared USB device context (used by MSC module)
 *
 * @return Pointer to usbd_context
 */
struct usbd_context *usb_cdc_get_usbd(void);

/**
 * @brief Send response data to USB CDC host
 *
 * @param data Response data
 * @param len Data length
 * @return Bytes sent, or negative error code
 */
int usb_cdc_send_response(const uint8_t *data, uint16_t len);

/**
 * @brief Check if USB CDC host is connected (DTR set)
 *
 * @return true if connected
 */
bool usb_cdc_is_connected(void);

#endif /* CLIP_USB_CDC_H */
