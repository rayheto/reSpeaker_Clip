/*
 * Copyright (c) 2025 Seeed Technology Co., Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Proxy disk driver sharing the SD card between the application (FATFS) and
 * the USB host (MSC). See sd_share.h for the model.
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/storage/disk_access.h>
#include <errno.h>

#include "sd_share.h"

LOG_MODULE_REGISTER(sd_share, CONFIG_CLIP_LOG_LEVEL);

/* The real SD disk registered by the SDMMC driver. */
#define SD_REAL_DISK "SD"

static atomic_t media_present = ATOMIC_INIT(1);

static int sd_share_status(struct disk_info *disk)
{
	ARG_UNUSED(disk);

	if (!atomic_get(&media_present)) {
		return DISK_STATUS_NOMEDIA;
	}

	return disk_access_status(SD_REAL_DISK);
}

static int sd_share_init_disk(struct disk_info *disk)
{
	ARG_UNUSED(disk);

	return disk_access_init(SD_REAL_DISK);
}

static int sd_share_read(struct disk_info *disk, uint8_t *data_buf,
			 uint32_t start_sector, uint32_t num_sector)
{
	ARG_UNUSED(disk);

	if (!atomic_get(&media_present)) {
		return -ENOMEDIUM;
	}

	return disk_access_read(SD_REAL_DISK, data_buf, start_sector, num_sector);
}

static int sd_share_write(struct disk_info *disk, const uint8_t *data_buf,
			  uint32_t start_sector, uint32_t num_sector)
{
	ARG_UNUSED(disk);

	if (!atomic_get(&media_present)) {
		return -ENOMEDIUM;
	}

	return disk_access_write(SD_REAL_DISK, data_buf, start_sector, num_sector);
}

static int sd_share_ioctl(struct disk_info *disk, uint8_t cmd, void *buffer)
{
	ARG_UNUSED(disk);

	if (!atomic_get(&media_present)) {
		/* Also fail sector-count/size queries so the SCSI layer
		 * reports "no media present" consistently everywhere. */
		return -ENOMEDIUM;
	}

	return disk_access_ioctl(SD_REAL_DISK, cmd, buffer);
}

static const struct disk_operations sd_share_ops = {
	.init = sd_share_init_disk,
	.status = sd_share_status,
	.read = sd_share_read,
	.write = sd_share_write,
	.ioctl = sd_share_ioctl,
};

static struct disk_info sd_share_disk = {
	.name = SD_SHARE_DISK_NAME,
	.ops = &sd_share_ops,
};

void sd_share_init(void)
{
	int err = disk_access_register(&sd_share_disk);

	if (err) {
		LOG_ERR("disk_access_register(%s): %d", SD_SHARE_DISK_NAME, err);
	}
}

void sd_share_set_media(bool present)
{
	bool was = atomic_set(&media_present, present ? 1 : 0);

	if ((bool)was != present) {
		LOG_INF("MSC media %s", present ? "present" : "ejected");
	}
}

bool sd_share_media_present(void)
{
	return atomic_get(&media_present) != 0;
}
