// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * RAVE2 Message Queue Driver
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */
#include "rave_mq_cdev.h"

#include <linux/circ_buf.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/mailbox_controller.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/types.h>
#include <linux/version.h>

#define RAVE_MQ_MBOX_CHANS	4

#define RAVE_MQ_BUFFER_SIZE	(0x1000)
#define RAVE_MQ_DATA_OFFSET	(0x1000)

#define RAVE_MQ_MAGIC		(0x00)
#define RAVE_MQ_SIZE		(0x04)
#define RAVE_MQ_OFFSET		(0x08)
#define RAVE_MQ_HEAD		(0x0C)
#define RAVE_MQ_TAIL		(0x10)

enum rave_mq_direction {
	RAVE_MQ_RX,
	RAVE_MQ_TX,
};

struct rave_mq;

/**
 * struct rave_mq_tunnel - Tunnel for message transmission/reception
 * @mq: Parent message queue
 * @direction: Tunnel direction (RX or TX)
 * @mem: Memory-mapped IO region
 * @header: Header offset
 * @data: Data offset
 * @sig: Signal GPIO descriptor
 * @ack: Acknowledge GPIO descriptor
 * @irq: IRQ number
 * @lock: Spinlock protecting tunnel header / buffer access
 */
struct rave_mq_tunnel {
	struct rave_mq			*mq;
	enum rave_mq_direction		direction;
	void __iomem			*mem;
	u32				header;
	u32				data;
	union {
		struct gpio_desc	*sig;
		struct gpio_desc	*ack;
	};
	int				irq;
	/* Protects tunnel header / buffer access */
	spinlock_t			lock;
};

/**
 * struct rave_mq - Main RAVE MQ device structure
 * @dev: Device pointer
 * @cdev: Character device handle
 * @chans: Mailbox channels
 * @mbox: Mailbox controller
 * @rx: RX tunnel
 * @tx: TX tunnel
 */
struct rave_mq {
	struct device		*dev;
	struct rave_mq_cdev	*cdev;
	struct mbox_chan	chans[RAVE_MQ_MBOX_CHANS];
	struct mbox_controller	mbox;
	struct rave_mq_tunnel	rx;
	struct rave_mq_tunnel	tx;
};

/* ----------------------------------------------------------------------------
 * Tunnel Operations
*/

/**
 * rave_mq_tunn_write - Write a value to the MQ memory region
 * @tunn: MQ tunnel
 * @offset: Offset in memory region
 * @value: Value to write
 */
static inline void rave_mq_tunn_write(struct rave_mq_tunnel *tunn, u32 offset,
				      u32 value)
{
	writel(value, tunn->mem + offset);
}

/**
 * rave_mq_tunn_read - Read a value from the MQ memory region
 * @tunn: MQ tunnel
 * @offset: Offset in memory region
 *
 * Returns: Value read from memory
 */
static inline u32 rave_mq_tunn_read(struct rave_mq_tunnel *tunn, u32 offset)
{
	return readl(tunn->mem + offset);
}

/**
 * rave_mq_tunn_get_buffer_locked - Get buffer parameters (locked)
 * @tunn: MQ tunnel
 * @size: Buffer size output
 * @offset: Buffer offset output
 * @head: Buffer head output
 * @tail: Buffer tail output
 *
 * Must be called with tunnel lock held.
 */
static void rave_mq_tunn_get_buffer_locked(struct rave_mq_tunnel *tunn,
					   u32 *size, u32 *offset, u32 *head,
					   u32 *tail)
{
	lockdep_assert_held(&tunn->lock);

	*size = rave_mq_tunn_read(tunn, tunn->header + RAVE_MQ_SIZE);
	*offset = rave_mq_tunn_read(tunn, tunn->header + RAVE_MQ_OFFSET);
	*head = rave_mq_tunn_read(tunn, tunn->header + RAVE_MQ_HEAD);
	*tail = rave_mq_tunn_read(tunn, tunn->header + RAVE_MQ_TAIL);
}

/**
 * rave_mq_tunn_get_buffer - Get buffer parameters (thread-safe)
 * @tunn: MQ tunnel
 * @size: Buffer size output
 * @offset: Buffer offset output
 * @head: Buffer head output
 * @tail: Buffer tail output
 */
static void rave_mq_tunn_get_buffer(struct rave_mq_tunnel *tunn, u32 *size,
				    u32 *offset, u32 *head, u32 *tail)
{
	unsigned long flags;

	spin_lock_irqsave(&tunn->lock, flags);
	rave_mq_tunn_get_buffer_locked(tunn, size, offset, head, tail);
	spin_unlock_irqrestore(&tunn->lock, flags);
}

/**
 * rave_mq_tunn_recv - Receive a message from MQ tunnel
 * @tunn: MQ tunnel (RX)
 *
 * Returns: 0 on success, negative error code otherwise
 */
static int rave_mq_tunn_recv(struct rave_mq_tunnel *tunn)
{
	struct rave_mq *mq = tunn->mq;
	struct rave_mq_msg msg;
	u32 size, head, tail, offset;
	int i;

	if (tunn->direction == RAVE_MQ_TX)
		return -EIO;

	/* FIXME: race condition - we should updated tail while locked */
	rave_mq_tunn_get_buffer(tunn, &size, &offset, &head, &tail);

	if (!CIRC_CNT(head, tail, size))
		return -EWOULDBLOCK;

	offset += tail * sizeof(msg);
	msg.magic = rave_mq_tunn_read(tunn, offset);
	offset += sizeof(u32);
	msg.type = rave_mq_tunn_read(tunn, offset);
	offset += sizeof(u32);
	msg.payload = rave_mq_tunn_read(tunn, offset);
	offset += sizeof(u32);
	msg.payload |= (u64)rave_mq_tunn_read(tunn, offset) << 32;

	rave_mq_tunn_write(tunn, tunn->header + RAVE_MQ_TAIL,
			   (tail + 1) % size);

	for (i = 0; i < RAVE_MQ_MBOX_CHANS; i++) {
		struct mbox_chan *chan = &mq->chans[i];

		if (chan->cl)
			mbox_chan_received_data(chan, &msg);
	}

	return 0;
}

/**
 * rave_mq_tunn_send - Send a message via MQ tunnel
 * @tunn: MQ tunnel (TX)
 * @msg: Message to send
 *
 * Returns: 0 on success, negative error code otherwise
 */
static int rave_mq_tunn_send(struct rave_mq_tunnel *tunn,
			     const struct rave_mq_msg *msg)
{
	u32 size, head, tail, offset;

	if (tunn->direction == RAVE_MQ_RX)
		return -EIO;

	if (msg->magic != RAVE_MQ_MESSAGE_MAGIC)
		return -EPROTO;

	/* FIXME: race condition - we should updated head while locked */
	rave_mq_tunn_get_buffer(tunn, &size, &offset, &head, &tail);

	if (!CIRC_SPACE(head, tail, size))
		return -EWOULDBLOCK;

	offset += head * sizeof(*msg);
	rave_mq_tunn_write(tunn, offset, msg->magic);
	offset += sizeof(u32);
	rave_mq_tunn_write(tunn, offset, msg->type);
	offset += sizeof(u32);
	rave_mq_tunn_write(tunn, offset, lower_32_bits(msg->payload));
	offset += sizeof(u32);
	rave_mq_tunn_write(tunn, offset, upper_32_bits(msg->payload));

	rave_mq_tunn_write(tunn, RAVE_MQ_HEAD, (head + 1) % size);

	gpiod_set_value(tunn->sig, 1);

	return 0;
}

/**
 * rave_mq_tunn_irq_handler - IRQ handler for MQ tunnel
 * @irq: IRQ number
 * @data: Pointer to MQ tunnel
 *
 * Returns: IRQ_HANDLED
 */
static irqreturn_t rave_mq_tunn_irq_handler(int irq, void *data)
{
	struct rave_mq_tunnel *tunn = data;

	if (!gpiod_get_value(tunn->ack))
		return IRQ_NONE;

	gpiod_set_value(tunn->ack, 0);

	rave_mq_tunn_recv(tunn);

	return IRQ_HANDLED;
}

/**
 * rave_mq_tunn_buffer_init - Initialize MQ buffer
 * @tunn: MQ tunnel
 */
static void rave_mq_tunn_buffer_init(struct rave_mq_tunnel *tunn)
{
	if (rave_mq_tunn_read(tunn, RAVE_MQ_MAGIC) == RAVE_MQ_BUFFER_MAGIC)
		return;

	rave_mq_tunn_write(tunn, RAVE_MQ_MAGIC, RAVE_MQ_BUFFER_MAGIC);
	rave_mq_tunn_write(tunn, RAVE_MQ_SIZE, RAVE_MQ_BUFFER_SIZE /
					       sizeof(struct rave_mq_msg));
	rave_mq_tunn_write(tunn, RAVE_MQ_OFFSET, RAVE_MQ_DATA_OFFSET);
	rave_mq_tunn_write(tunn, RAVE_MQ_HEAD, 0);
	rave_mq_tunn_write(tunn, RAVE_MQ_TAIL, 0);
};

/**
 * rave_mq_tunn_init - Initialize MQ tunnel
 * @pdev: Platform device
 * @tunn: MQ tunnel to initialize
 * @dir: Tunnel direction
 *
 * Returns: 0 on success, negative error code otherwise
 */
static int rave_mq_tunn_init(struct platform_device *pdev,
			     struct rave_mq_tunnel *tunn,
			     enum rave_mq_direction dir)
{
	struct rave_mq *mq = tunn->mq;
	const char *iom = dir == RAVE_MQ_TX ? "tx-bram" : "rx-bram";
	int ret;

	tunn->direction = dir;
	tunn->header = 0;
	tunn->data = RAVE_MQ_DATA_OFFSET;
	spin_lock_init(&tunn->lock);

	tunn->mem = devm_platform_ioremap_resource_byname(pdev, iom);
	if (IS_ERR(tunn->mem))
		return dev_err_probe(mq->dev, PTR_ERR(tunn->mem),
				     "failed to map %s memory\n", iom);

	if (dir == RAVE_MQ_TX) {
		tunn->sig = devm_gpiod_get(mq->dev, "tx", GPIOD_OUT_LOW);
		if (IS_ERR(tunn->sig))
			return dev_err_probe(mq->dev, PTR_ERR(tunn->sig),
					     "failed to get sig gpio\n");
	} else {
		tunn->ack = devm_gpiod_get(mq->dev, "rx", GPIOD_OUT_LOW);
		if (IS_ERR(tunn->ack))
			return dev_err_probe(mq->dev, PTR_ERR(tunn->ack),
					     "failed to get ack gpio\n");

		ret = of_property_read_u32(pdev->dev.of_node, "amd,msix-irq",
					   &tunn->irq);
		if (ret < 0) {
			tunn->irq = platform_get_irq(pdev, 0);
			if (tunn->irq < 0)
				return dev_err_probe(mq->dev, tunn->irq,
						     "failed to get irq\n");
		}

		ret = devm_request_threaded_irq(mq->dev, tunn->irq, NULL,
						rave_mq_tunn_irq_handler,
						IRQF_ONESHOT | IRQF_SHARED,
						"rave-mq", tunn);
		if (ret < 0)
			return dev_err_probe(mq->dev, ret,
					     "failed to request irq\n");
	}

	rave_mq_tunn_buffer_init(tunn);

	return 0;
}

/* ----------------------------------------------------------------------------
 * Mailbox Controller
*/

static int rave_mq_chan_send(struct mbox_chan *chan, void *data)
{
	struct rave_mq *mq = chan->con_priv;
	struct rave_mq_tunnel *tunn = &mq->tx;
	struct rave_mq_msg *msg = data;

	return rave_mq_tunn_send(tunn, msg);
}

static const struct mbox_chan_ops rave_mq_mbox_ops = {
	.send_data = rave_mq_chan_send,
};

static int rave_mq_controller_init(struct rave_mq *mq)
{
	int i;

	mq->mbox.dev = mq->dev;
	mq->mbox.chans = mq->chans;
	mq->mbox.num_chans = RAVE_MQ_MBOX_CHANS;
	mq->mbox.ops = &rave_mq_mbox_ops;

	for (i = 0; i < RAVE_MQ_MBOX_CHANS; i++)
		mq->chans[i].con_priv = mq;

	return devm_mbox_controller_register(mq->dev, &mq->mbox);
}

/* ----------------------------------------------------------------------------
 * Platform Driver
 */

static int rave_mq_probe(struct platform_device *pdev)
{
	struct rave_mq *mq;
	struct device *dev = &pdev->dev;
	int ret;

	mq = devm_kzalloc(dev, sizeof(*mq), GFP_KERNEL);
	if (!mq)
		return -ENOMEM;

	mq->dev = dev;
	platform_set_drvdata(pdev, mq);

	mq->rx.mq = mq;
	mq->tx.mq = mq;

	ret = rave_mq_tunn_init(pdev, &mq->rx, RAVE_MQ_RX);
	if (ret < 0)
		return ret;

	ret = rave_mq_tunn_init(pdev, &mq->tx, RAVE_MQ_TX);
	if (ret < 0)
		return ret;

	ret = rave_mq_controller_init(mq);
	if (ret < 0)
		return dev_err_probe(mq->dev, ret,
				     "failed to register mailbox controller\n");

	mq->cdev = rave_mq_cdev_init(mq->dev);
	if (IS_ERR(mq->cdev))
		return PTR_ERR(mq->cdev);

	dev_info(mq->dev, "RAVE MQ device probed\n");

	return 0;
}

static void rave_mq_remove(struct platform_device *pdev)
{
	struct rave_mq *mq = platform_get_drvdata(pdev);

	rave_mq_cdev_fini(mq->cdev);

	dev_info(mq->dev, "RAVE MQ device removed\n");
}

static const struct of_device_id rave_mq_of_match[] = {
	{ .compatible = "amd,rave-message-queue-1.0" },
	{}
};
MODULE_DEVICE_TABLE(of, rave_mq_of_match);

static struct platform_driver rave_mq_drv = {
	.probe = rave_mq_probe,
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 11, 0)
	.remove = rave_mq_remove,
#else
	.remove_new = rave_mq_remove,
#endif
	.driver = {
		.name = "rave-mq",
		.of_match_table = rave_mq_of_match,
	}
};
module_platform_driver(rave_mq_drv);

MODULE_AUTHOR("Anatoliy Klymenko");
MODULE_DESCRIPTION("RAVE2 Message Queue Driver");
MODULE_LICENSE("GPL");
