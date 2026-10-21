// SPDX-License-Identifier: MIT
// Contributors: AK
// Maintainer: AK
/*
 * RAVE2 Message Queue Library
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */

#include "rave_mq.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdlib.h>
#include <unistd.h>

#define RAVE_MQ_MAGIC  ((0xBADAUL << 16) | 0xB00EUL)

struct rave_mq {
	int fd;
};

/**
 * @brief Wait for a file descriptor to become ready for IO.
 *
 * Uses blocking poll and retries on interrupted syscalls.
 *
 * @param fd File descriptor to poll.
 * @param events Poll events mask (e.g. POLLIN, POLLOUT).
 *
 * @return 0 on success, otherwise an errno-style error value.
 */
static int rave_mq_wait(int fd, short events)
{
	struct pollfd pfd = {
		.fd = fd,
		.events = events,
		.revents = 0,
	};

	for (;;) {
		int ret = poll(&pfd, 1, -1);

		if (ret > 0)
			return 0;
		if (ret == 0)
			continue;
		if (errno == EINTR)
			continue;

		return errno;
	}
}

/**
 * @brief Open the RAVE MQ device.
 *
 * Opens `/dev/rave-mq` for read/write access and returns an opaque handle.
 *
 * @return Pointer to an initialized `rave_mq` handle on success, otherwise
 *         `NULL` with `errno` set.
 */
rave_mq *rave_mq_open(void)
{
	rave_mq *mq;
	int fd;

	fd = open("/dev/rave-mq", O_RDWR | O_CLOEXEC);
	if (fd < 0)
		return NULL;

	mq = static_cast<rave_mq *>(calloc(1, sizeof(*mq)));
	if (!mq) {
		close(fd);
		errno = ENOMEM;
		return NULL;
	}

	mq->fd = fd;

	return mq;
}

/**
 * @brief Close a previously opened RAVE MQ handle.
 *
 * Safe to call with `NULL`.
 *
 * @param mq Opaque RAVE MQ handle returned by `rave_mq_open()`.
 */
void rave_mq_close(rave_mq *mq)
{
	if (!mq)
		return;

	close(mq->fd);
	free(mq);
}

/**
 * @brief Send a message to the RAVE MQ device.
 *
 * Waits until the device is writable, injects the protocol magic into the
 * message header, and writes exactly one message.
 *
 * @param mq Opaque RAVE MQ handle.
 * @param msg Message to transmit.
 *
 * @return 0 on success, or a negative errno value on failure.
 */
int rave_mq_send(rave_mq *mq, const struct rave_mq_msg *msg)
{
	if (!mq || !msg)
		return -EINVAL;

	auto ret = rave_mq_wait(mq->fd, POLLOUT);
	if (ret < 0)
		return ret;

	const_cast<rave_mq_msg *>(msg)->reserved = RAVE_MQ_MAGIC;

	auto written = write(mq->fd, msg, sizeof(*msg));
	if (written < 0)
		return -errno;

	if (written != static_cast<ssize_t>(sizeof(*msg)))
		return -EIO;

	return 0;
}

/**
 * @brief Receive a message from the RAVE MQ device.
 *
 * Waits until the device is readable, reads exactly one message, and validates
 * the protocol magic.
 *
 * @param mq Opaque RAVE MQ handle.
 * @param msg Output message buffer.
 *
 * @return 0 on success, or a negative errno value on failure.
 */
int rave_mq_recv(rave_mq *mq, struct rave_mq_msg *msg)
{
	ssize_t read_count;

	if (!mq || !msg)
		return -EINVAL;

	auto ret = rave_mq_wait(mq->fd, POLLIN);
	if (ret < 0)
		return ret;

	read_count = read(mq->fd, msg, sizeof(*msg));
	if (read_count < 0)
		return -errno;

	if (read_count != static_cast<ssize_t>(sizeof(*msg)))
		return -EIO;

	if (msg->reserved != RAVE_MQ_MAGIC)
		return -EPROTO;

	return 0;
}
