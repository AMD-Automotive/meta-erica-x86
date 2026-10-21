/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * RAVE2 Message Queue Driver - Character Device Interface
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */

#ifndef __RAVE_MQ_CDEV_H__
#define __RAVE_MQ_CDEV_H__

#include <linux/device.h>

#define RAVE_MQ_DEV_NAME	"rave-mq"
#define RAVE_MQ_HI_MAGIC	(0xBADA << 16)
#define RAVE_MQ_BUFFER_MAGIC	(RAVE_MQ_HI_MAGIC | 0xB00F)
#define RAVE_MQ_MESSAGE_MAGIC	(RAVE_MQ_HI_MAGIC | 0xB00E)

enum rave_mq_msg_type {
	RAVE_MQ_MSG_START,
	RAVE_MQ_MSG_STOP,
	RAVE_MQ_MSG_PAUSE,
	RAVE_MQ_MSG_PAGE_FLIP,
	RAVE_MQ_MSG_VBLANK,
	RAVE_MQ_MSG_KEEP_ALIVE,
};

/**
 * struct rave_mq_msg - Message structure for RAVE MQ
 * @magic: Magic number for message validation
 * @type: Message type identifier
 * @payload: 64-bit payload data
 */
struct rave_mq_msg {
	u32	magic;
	u32	type;
	u64	payload;
} __packed;


struct rave_mq_cdev;

struct rave_mq_cdev *rave_mq_cdev_init(struct device *dev);
void rave_mq_cdev_fini(struct rave_mq_cdev *cdev);

#endif /* __RAVE_MQ_CDEV_H__ */