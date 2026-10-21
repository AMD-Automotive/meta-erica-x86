// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * RAVE2 Message Queue Driver - Character Device Interface
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */
#include "rave_mq_cdev.h"

#include <linux/cdev.h>
#include <linux/circ_buf.h>
#include <linux/mailbox_client.h>
#include <linux/poll.h>
#include <linux/wait.h>

#define RAVE_MQ_CLIENT_COUNT	(4)
#define RAVE_MQ_MSG_COUNT	(16)

struct rave_mq_cdev;

struct rave_mq_client {
	struct mbox_client	mbox_client;
	struct mbox_chan	*mbox_chan;
	struct rave_mq_msg	msg_queue[RAVE_MQ_MSG_COUNT];
	unsigned int		msg_head;
	unsigned int		msg_tail;
	wait_queue_head_t	wait;
};

struct rave_mq_cdev {
	struct class		*class;
	struct device		*dev;
	struct device		*parent;
	struct cdev		cdev;
	dev_t			devno;
	struct rave_mq_client	clients[RAVE_MQ_CLIENT_COUNT];
};

static inline struct rave_mq_cdev *cdev_to_rave(struct cdev *cdev)
{
	return container_of(cdev, struct rave_mq_cdev, cdev);
}

static inline struct rave_mq_client *mbox_to_rave(struct mbox_client *client)
{
	return container_of(client, struct rave_mq_client, mbox_client);
}

static void rave_mq_rx_callback(struct mbox_client *client, void *data)
{
	struct rave_mq_client *mq_client = mbox_to_rave(client);
	struct rave_mq_msg *msg = data;

	/* Validate message */
	if (msg->magic != RAVE_MQ_MESSAGE_MAGIC)
		return;

	/* Check for queue overflow */
	if (!CIRC_SPACE(mq_client->msg_head, mq_client->msg_tail,
			RAVE_MQ_MSG_COUNT)) {
		dev_warn(mq_client->mbox_client.dev,
			 "message queue overflow, dropping message\n");
		return;
	}

	/* Enqueue message */
	mq_client->msg_queue[mq_client->msg_head] = *msg;
	mq_client->msg_head = (mq_client->msg_head + 1) % RAVE_MQ_MSG_COUNT;

	wake_up(&mq_client->wait);
}

static int rave_mq_cdev_open(struct inode *inode, struct file *filp)
{
	struct rave_mq_cdev *cdev = cdev_to_rave(inode->i_cdev);
	struct rave_mq_client *client = NULL;
	int ret, i;



	/* Search for unused client slot */
	for (i = 0; i < RAVE_MQ_CLIENT_COUNT; ++i)
		if (!cdev->clients[i].mbox_chan)
			break;

	if (i == RAVE_MQ_CLIENT_COUNT)
		return -EBUSY;

	client = &cdev->clients[i];
	client->mbox_client.dev = cdev->parent;
	client->mbox_client.tx_block = false;
	client->mbox_client.knows_txdone = true;
	init_waitqueue_head(&client->wait);
	client->mbox_client.rx_callback = rave_mq_rx_callback;
	client->msg_head = client->msg_tail = 0;

	client->mbox_chan = mbox_request_channel(&client->mbox_client, i);
	if (IS_ERR(client->mbox_chan)) {
		ret = PTR_ERR(client->mbox_chan);
		client->mbox_chan = NULL;
		dev_warn(cdev->dev, "failed to request mbox chan %d\n", i);
		return ret;
	}

	filp->private_data = client;

	return 0;
};

static int rave_mq_cdev_release(struct inode *inode, struct file *filp)
{
	struct rave_mq_client *client = filp->private_data;

	if (client->mbox_chan) {
		mbox_free_channel(client->mbox_chan);
		client->mbox_chan = NULL;
	}

	return 0;
};

static ssize_t rave_mq_cdev_read(struct file *filp, char __user *buf,
				 size_t size, loff_t *offset)
{
	struct rave_mq_client *client = filp->private_data;
	struct rave_mq_msg *msg;

	if (*offset || size < sizeof(*msg))
		return -EINVAL;

	if (!CIRC_CNT(client->msg_head, client->msg_tail, RAVE_MQ_MSG_COUNT))
		return -EWOULDBLOCK;

	msg = &client->msg_queue[client->msg_tail];
	client->msg_tail = (client->msg_tail + 1) % RAVE_MQ_MSG_COUNT;

	if (copy_to_user(buf, msg, sizeof(*msg)) > size - sizeof(*msg))
		return -EFAULT;

	return sizeof(*msg);
}

static ssize_t rave_mq_cdev_write(struct file *filp, const char __user *buf,
				  size_t size, loff_t *offset)
{
	struct rave_mq_client *client = filp->private_data;
	struct rave_mq_msg msg;
	int ret;

	if (*offset || size < sizeof(msg))
		return -EINVAL;

	if (copy_from_user(&msg, buf, sizeof(msg)) > size - sizeof(msg))
		return -EFAULT;

	ret = mbox_send_message(client->mbox_chan, &msg);
	if (ret < 0)
		return ret;

	mbox_client_txdone(client->mbox_chan, 0);

	return sizeof(msg);
}

static __poll_t rave_mq_cdev_poll(struct file *filp,
				  struct poll_table_struct *wait)
{
	struct rave_mq_client *client = filp->private_data;
	__poll_t mask = 0;

	poll_wait(filp, &client->wait, wait);

	mask |= POLLOUT | POLLWRNORM; /* Always writable */

	if (CIRC_CNT(client->msg_head, client->msg_tail, RAVE_MQ_MSG_COUNT))
		mask |= POLLIN | POLLRDNORM;

	return mask;
};

static const struct file_operations rave_mq_fops = {
	.owner		= THIS_MODULE,
	.open		= rave_mq_cdev_open,
	.release	= rave_mq_cdev_release,
	.read		= rave_mq_cdev_read,
	.write		= rave_mq_cdev_write,
	.poll		= rave_mq_cdev_poll,
};

struct rave_mq_cdev *rave_mq_cdev_init(struct device *dev)
{
	struct rave_mq_cdev *cdev;
	int ret;

	cdev = devm_kzalloc(dev, sizeof(*cdev), GFP_KERNEL);
	if (!cdev)
		return ERR_PTR(-ENOMEM);

	cdev->parent = dev;

	cdev->class = class_create(RAVE_MQ_DEV_NAME);
	if (IS_ERR(cdev->class)) {
		dev_err_probe(dev, PTR_ERR(cdev->class),
			      "failed to create device class\n");
		return ERR_CAST(cdev->class);
	}

	ret = alloc_chrdev_region(&cdev->devno, 0, 1, RAVE_MQ_DEV_NAME);
	if (ret < 0) {
		dev_err_probe(dev, ret,
			      "failed to allocate char device region\n");
		class_destroy(cdev->class);
		return ERR_PTR(ret);
	}

	cdev->cdev.owner = THIS_MODULE;
	cdev_init(&cdev->cdev, &rave_mq_fops);

	ret = cdev_add(&cdev->cdev, cdev->devno, 1);
	if (ret < 0) {
		dev_err_probe(dev, ret, "failed to add char device\n");
		unregister_chrdev_region(cdev->devno, 1);
		class_destroy(cdev->class);
		return ERR_PTR(ret);
	}

	cdev->dev = device_create(cdev->class, dev, cdev->devno, NULL,
				  RAVE_MQ_DEV_NAME);
	if (IS_ERR(cdev->dev)) {
		dev_err_probe(dev, PTR_ERR(cdev->dev),
			      "failed to create device\n");
		cdev_del(&cdev->cdev);
		unregister_chrdev_region(cdev->devno, 1);
		class_destroy(cdev->class);
		return ERR_CAST(cdev->dev);
	}

	return cdev;
}

void rave_mq_cdev_fini(struct rave_mq_cdev *cdev)
{
	if (!cdev)
		return;

	device_destroy(cdev->class, cdev->devno);
	cdev_del(&cdev->cdev);
	unregister_chrdev_region(cdev->devno, 1);
	class_destroy(cdev->class);
}