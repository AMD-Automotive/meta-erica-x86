// SPDX-License-Identifier: MIT
// Contributors: AK
// Maintainer: AK
/*
 * RAVE2 Message Queue Library
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */
#ifndef __RAVE_MQ_H__
#define __RAVE_MQ_H__

#if defined(__cplusplus)
extern "C" {
#endif

#include <stdint.h>

enum rave_mq_msg_type {
	RAVE_MQ_MSG_START,
	RAVE_MQ_MSG_STOP,
	RAVE_MQ_MSG_PAUSE,
	RAVE_MQ_MSG_PAGE_FLIP,
	RAVE_MQ_MSG_VBLANK,
	RAVE_MQ_MSG_KEEP_ALIVE,
};

struct rave_mq_msg {
	uint32_t	reserved;
	uint32_t	type;
	uint64_t	payload;
} __attribute__((packed));

typedef struct rave_mq rave_mq;

rave_mq *rave_mq_open(void);
void rave_mq_close(rave_mq *mq);
int rave_mq_send(rave_mq *mq, const struct rave_mq_msg *msg);
int rave_mq_recv(rave_mq *mq, struct rave_mq_msg *msg);

#if defined(__cplusplus)
}
#endif

#endif /* __RAVE_MQ_H__ */