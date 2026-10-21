// SPDX-License-Identifier: GPL-2.0-only
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: BW
// Maintainer: BW
/*
 * mdb5_channel.c - Per-channel V4L2 device for MDB5 video bridge
 *
 * Each channel is a V4L2 capture device backed by videobuf2-dma-contig.
 * The format is static (discovered from BRAM at probe time).
 * Supports mmap and dmabuf exporter modes.
 */

#include <linux/version.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/interrupt.h>
#include <linux/videodev2.h>
#include <media/v4l2-device.h>
#include <media/v4l2-ioctl.h>
#include <media/v4l2-event.h>
#include <media/videobuf2-v4l2.h>
#include <media/videobuf2-dma-sg.h>

#include "mdb5_internal.h"

/* ----------------------------------------------------------------
 * videobuf2 queue operations
 * ---------------------------------------------------------------- */

static int mdb5_queue_setup(struct vb2_queue *q,
			    unsigned int *num_buffers,
			    unsigned int *num_planes,
			    unsigned int sizes[],
			    struct device *alloc_devs[])
{
	struct mdb5_channel *ch = vb2_get_drv_priv(q);

	if (*num_planes) {
		/* Called with already-set plane info (REQBUFS with count > 0) */
		if (sizes[0] < ch->pix_fmt.sizeimage)
			return -EINVAL;
		return 0;
	}

	*num_planes = 1;
	sizes[0] = ch->pix_fmt.sizeimage;

	dev_dbg(&ch->mdev->pdev->dev,
		"Ch%u: queue_setup: %u buffers, size=%u\n",
		ch->index, *num_buffers, sizes[0]);
	return 0;
}

static int mdb5_buf_prepare(struct vb2_buffer *vb)
{
	struct mdb5_channel *ch = vb2_get_drv_priv(vb->vb2_queue);

	if (vb2_plane_size(vb, 0) < ch->pix_fmt.sizeimage) {
		dev_err(&ch->mdev->pdev->dev,
			"Ch%u: buffer too small (%lu < %u)\n",
			ch->index, vb2_plane_size(vb, 0),
			ch->pix_fmt.sizeimage);
		return -EINVAL;
	}

	vb2_set_plane_payload(vb, 0, ch->pix_fmt.sizeimage);
	return 0;
}

static void mdb5_buf_queue(struct vb2_buffer *vb)
{
	struct vb2_v4l2_buffer *vbuf = to_vb2_v4l2_buffer(vb);
	struct mdb5_buffer *buf = to_mdb5_buffer(vbuf);
	struct mdb5_channel *ch = vb2_get_drv_priv(vb->vb2_queue);
	unsigned long flags;

	spin_lock_irqsave(&ch->buf_lock, flags);
	list_add_tail(&buf->list, &ch->buf_list);
	spin_unlock_irqrestore(&ch->buf_lock, flags);
}

static int mdb5_start_streaming(struct vb2_queue *q, unsigned int count)
{
	struct mdb5_channel *ch = vb2_get_drv_priv(q);

	ch->sequence = 0;
	ch->streaming = true;

	dev_info(&ch->mdev->pdev->dev, "Ch%u: streaming started\n", ch->index);
	return 0;
}

static void mdb5_stop_streaming(struct vb2_queue *q)
{
	struct mdb5_channel *ch = vb2_get_drv_priv(q);
	struct mdb5_buffer *buf, *tmp;
	unsigned long flags;

	ch->streaming = false;

	/* Terminate any pending DMA on the shared channel to unblock an
	 * in-flight frame handler waiting on completion. */
	if (ch->mdev->dma_chan)
		dmaengine_terminate_sync(ch->mdev->dma_chan);

	/*
	 * Wait for any in-flight frame handler to finish before we hand the
	 * queue back to vb2.  mdb5_handle_frame() removes a buffer from
	 * buf_list, runs a long DMA with the buffer detached, then calls
	 * vb2_buffer_done().  Without this barrier that completion can land
	 * after __vb2_queue_cancel() has reclaimed the queue -> "buffer left
	 * in active state" warnings and a use-after-free panic on teardown.
	 * synchronize_irq() waits for both the hardirq and the threaded
	 * handler; new handlers bail on the !streaming check above.
	 */
	synchronize_irq(pci_irq_vector(ch->mdev->pdev,
				       ch->mdev->edma_nr_irqs + ch->index));

	/* Return all queued buffers to vb2 with error state */
	spin_lock_irqsave(&ch->buf_lock, flags);
	list_for_each_entry_safe(buf, tmp, &ch->buf_list, list) {
		list_del(&buf->list);
		vb2_buffer_done(&buf->vb.vb2_buf, VB2_BUF_STATE_ERROR);
	}
	spin_unlock_irqrestore(&ch->buf_lock, flags);

	dev_info(&ch->mdev->pdev->dev, "Ch%u: streaming stopped\n", ch->index);
}

static const struct vb2_ops mdb5_vb2_ops = {
	.queue_setup     = mdb5_queue_setup,
	.buf_prepare     = mdb5_buf_prepare,
	.buf_queue       = mdb5_buf_queue,
	.start_streaming = mdb5_start_streaming,
	.stop_streaming  = mdb5_stop_streaming,
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 8, 0)
	.wait_prepare    = vb2_ops_wait_prepare,
	.wait_finish     = vb2_ops_wait_finish,
#endif
};

/* ----------------------------------------------------------------
 * V4L2 ioctl operations
 * ---------------------------------------------------------------- */

static int mdb5_querycap(struct file *file, void *priv,
			 struct v4l2_capability *cap)
{
	struct mdb5_channel *ch = video_drvdata(file);

	strscpy(cap->driver, "mdb5_v4l2", sizeof(cap->driver));
	snprintf(cap->card, sizeof(cap->card),
		 "MDB5 Video Bridge Ch%u", ch->index);
	snprintf(cap->bus_info, sizeof(cap->bus_info),
		 "PCI:%s", pci_name(ch->mdev->pdev));
	return 0;
}

static int mdb5_enum_fmt(struct file *file, void *priv,
			 struct v4l2_fmtdesc *f)
{
	struct mdb5_channel *ch = video_drvdata(file);

	if (f->index > 0)
		return -EINVAL;

	f->pixelformat = ch->pix_fmt.pixelformat;
	return 0;
}

static int mdb5_g_fmt(struct file *file, void *priv,
		      struct v4l2_format *f)
{
	struct mdb5_channel *ch = video_drvdata(file);

	f->fmt.pix = ch->pix_fmt;
	return 0;
}

static int mdb5_s_fmt(struct file *file, void *priv,
		      struct v4l2_format *f)
{
	struct mdb5_channel *ch = video_drvdata(file);

	/* Format is static - just return what we have */
	f->fmt.pix = ch->pix_fmt;
	return 0;
}

static int mdb5_try_fmt(struct file *file, void *priv,
			struct v4l2_format *f)
{
	struct mdb5_channel *ch = video_drvdata(file);

	/* Only one format supported */
	f->fmt.pix = ch->pix_fmt;
	return 0;
}

static int mdb5_enum_input(struct file *file, void *priv,
			   struct v4l2_input *inp)
{
	if (inp->index > 0)
		return -EINVAL;

	strscpy(inp->name, "PCIe Video Bridge", sizeof(inp->name));
	inp->type = V4L2_INPUT_TYPE_CAMERA;
	return 0;
}

static int mdb5_g_input(struct file *file, void *priv, unsigned int *i)
{
	*i = 0;
	return 0;
}

static int mdb5_s_input(struct file *file, void *priv, unsigned int i)
{
	return (i == 0) ? 0 : -EINVAL;
}

static int mdb5_g_parm(struct file *file, void *priv,
		       struct v4l2_streamparm *sp)
{
	if (sp->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
		return -EINVAL;

	sp->parm.capture.capability = V4L2_CAP_TIMEPERFRAME;
	sp->parm.capture.timeperframe.numerator = 1;
	sp->parm.capture.timeperframe.denominator = 30;
	sp->parm.capture.readbuffers = 4;
	return 0;
}

static int mdb5_s_parm(struct file *file, void *priv,
		       struct v4l2_streamparm *sp)
{
	if (sp->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
		return -EINVAL;

	/* Accept but ignore — framerate is fixed by the endpoint */
	sp->parm.capture.capability = V4L2_CAP_TIMEPERFRAME;
	sp->parm.capture.timeperframe.numerator = 1;
	sp->parm.capture.timeperframe.denominator = 30;
	sp->parm.capture.readbuffers = 4;
	return 0;
}

static const struct v4l2_ioctl_ops mdb5_ioctl_ops = {
	.vidioc_querycap         = mdb5_querycap,
	.vidioc_enum_fmt_vid_cap = mdb5_enum_fmt,
	.vidioc_g_fmt_vid_cap    = mdb5_g_fmt,
	.vidioc_s_fmt_vid_cap    = mdb5_s_fmt,
	.vidioc_try_fmt_vid_cap  = mdb5_try_fmt,

	.vidioc_enum_input       = mdb5_enum_input,
	.vidioc_g_input          = mdb5_g_input,
	.vidioc_s_input          = mdb5_s_input,
	.vidioc_g_parm           = mdb5_g_parm,
	.vidioc_s_parm           = mdb5_s_parm,

	.vidioc_reqbufs          = vb2_ioctl_reqbufs,
	.vidioc_querybuf         = vb2_ioctl_querybuf,
	.vidioc_qbuf             = vb2_ioctl_qbuf,
	.vidioc_dqbuf            = vb2_ioctl_dqbuf,
	.vidioc_expbuf           = vb2_ioctl_expbuf,
	.vidioc_create_bufs      = vb2_ioctl_create_bufs,
	.vidioc_prepare_buf      = vb2_ioctl_prepare_buf,
	.vidioc_streamon         = vb2_ioctl_streamon,
	.vidioc_streamoff        = vb2_ioctl_streamoff,

	.vidioc_subscribe_event   = v4l2_ctrl_subscribe_event,
	.vidioc_unsubscribe_event = v4l2_event_unsubscribe,
};

/* ----------------------------------------------------------------
 * V4L2 file operations
 * ---------------------------------------------------------------- */

static const struct v4l2_file_operations mdb5_fops = {
	.owner          = THIS_MODULE,
	.open           = v4l2_fh_open,
	.release        = vb2_fop_release,
	.read           = vb2_fop_read,
	.mmap           = vb2_fop_mmap,
	.poll           = vb2_fop_poll,
	.unlocked_ioctl = video_ioctl2,
};

/* ----------------------------------------------------------------
 * Channel init / fini
 * ---------------------------------------------------------------- */

int mdb5_channel_init(struct mdb5_channel *ch)
{
	struct mdb5_dev *mdev = ch->mdev;
	struct pci_dev *pdev = mdev->pdev;
	int ret;

	INIT_LIST_HEAD(&ch->buf_list);
	spin_lock_init(&ch->buf_lock);
	mutex_init(&ch->vb2_lock);

	/* Register V4L2 device */
	ret = v4l2_device_register(&pdev->dev, &ch->v4l2_dev);
	if (ret) {
		dev_err(&pdev->dev, "Ch%u: v4l2_device_register failed: %d\n",
			ch->index, ret);
		return ret;
	}

	/* Initialize vb2 queue */
	ch->vb2_q.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	ch->vb2_q.io_modes = VB2_MMAP | VB2_DMABUF | VB2_READ;
	ch->vb2_q.dev = &pdev->dev;
	ch->vb2_q.drv_priv = ch;
	ch->vb2_q.buf_struct_size = sizeof(struct mdb5_buffer);
	ch->vb2_q.ops = &mdb5_vb2_ops;
	ch->vb2_q.mem_ops = &vb2_dma_sg_memops;
	ch->vb2_q.timestamp_flags = V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC;
	ch->vb2_q.min_queued_buffers = 2;
	ch->vb2_q.lock = &ch->vb2_lock;

	ret = vb2_queue_init(&ch->vb2_q);
	if (ret) {
		dev_err(&pdev->dev, "Ch%u: vb2_queue_init failed: %d\n",
			ch->index, ret);
		goto err_v4l2;
	}

	/* Initialize video device */
	strscpy(ch->vdev.name, "mdb5_v4l2", sizeof(ch->vdev.name));
	ch->vdev.release = video_device_release_empty;
	ch->vdev.fops = &mdb5_fops;
	ch->vdev.ioctl_ops = &mdb5_ioctl_ops;
	ch->vdev.v4l2_dev = &ch->v4l2_dev;
	ch->vdev.queue = &ch->vb2_q;
	ch->vdev.lock = &ch->vb2_lock;
	ch->vdev.device_caps = V4L2_CAP_VIDEO_CAPTURE |
			       V4L2_CAP_STREAMING |
			       V4L2_CAP_READWRITE;
	video_set_drvdata(&ch->vdev, ch);

	ret = video_register_device(&ch->vdev, VFL_TYPE_VIDEO, -1);
	if (ret) {
		dev_err(&pdev->dev,
			"Ch%u: video_register_device failed: %d\n",
			ch->index, ret);
		goto err_v4l2;
	}

	dev_info(&pdev->dev, "Ch%u: registered as /dev/video%d\n",
		 ch->index, ch->vdev.num);
	return 0;

err_v4l2:
	v4l2_device_unregister(&ch->v4l2_dev);
	return ret;
}

void mdb5_channel_fini(struct mdb5_channel *ch)
{
	video_unregister_device(&ch->vdev);
	v4l2_device_unregister(&ch->v4l2_dev);
}
