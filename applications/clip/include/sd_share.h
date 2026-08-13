/*
 * Copyright (c) 2025 Seeed Technology Co., Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef SD_SHARE_H
#define SD_SHARE_H

#include <stdbool.h>

/**
 * @brief SD card sharing between the app and the USB MSC host
 *
 * The USB MSC LUN is attached to a proxy disk ("SDC") instead of the real
 * SD disk ("SD"). The proxy forwards all access to the real disk while the
 * media is flagged present, and reports "no media" (DISK_STATUS_NOMEDIA /
 * -ENOMEDIUM) once ejected. The Zephyr MSC SCSI layer turns that into
 * NOT READY / MEDIUM NOT PRESENT responses on the wire, i.e. the host sees
 * an empty card reader slot, while the USB device (and the CDC console)
 * stays enumerated the whole time.
 */

/** Disk name of the proxy the MSC LUN must be attached to. */
#define SD_SHARE_DISK_NAME "SDC"

/**
 * @brief Register the proxy disk with the disk_access layer.
 *
 * Call once at boot before the USB stack is enabled.
 */
void sd_share_init(void);

/**
 * @brief Eject or insert the host-visible media.
 *
 * @param present true: host may access the card; false: all SCSI access is
 *                answered with "medium not present" so the app can own the
 *                card (mount FATFS, record, transfer).
 */
void sd_share_set_media(bool present);

/**
 * @brief Check whether the media is currently exposed to the host.
 */
bool sd_share_media_present(void);

#endif /* SD_SHARE_H */
