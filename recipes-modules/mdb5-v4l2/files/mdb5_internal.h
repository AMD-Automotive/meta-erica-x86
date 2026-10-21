/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
/* Contributors: BW
/* Maintainer: BW
/*
 * mdb5_internal.h - Internal driver structures and prototypes
 */
#ifndef MDB5_INTERNAL_H
#define MDB5_INTERNAL_H

#include <linux/types.h>
#include <linux/pci.h>
#include <linux/mutex.h>
#include <linux/spinlock.h>
#include <linux/list.h>
#include <linux/videodev2.h>
#include <linux/dmaengine.h>
#include <linux/dma/edma.h>
#include <media/v4l2-device.h>
#include <media/v4l2-ioctl.h>
#include <media/v4l2-ctrls.h>
#include <media/videobuf2-v4l2.h>
#include <media/videobuf2-dma-sg.h>

#include "mdb5_bridge.h"

struct mdb5_dev;
struct mdb5_channel;

/*
 * vb2 buffer wrapper - embeds the vb2_v4l2_buffer and a list node
 * for tracking queued buffers.
 */
struct mdb5_buffer {
	struct vb2_v4l2_buffer  vb;
	struct list_head        list;
};

static inline struct mdb5_buffer *to_mdb5_buffer(struct vb2_v4l2_buffer *vb)
{
	return container_of(vb, struct mdb5_buffer, vb);
}

/*
 * Per-channel in-band watermark statistics.
 *
 * Populated in mdb5_handle_frame() when wm_decode is enabled: the driver
 * decodes the watermark the endpoint stamped into each frame and compares
 * it against the mailbox metadata.  Exposed read-only via debugfs
 * (ch<N>_wm_stats) for monitoring and for the order-integrity soak test.
 */
struct mdb5_wm_stats {
	u64  frames;             /* frames delivered while decoding */
	u64  wm_valid;           /* frames with a decodable watermark */
	u64  wm_corrupt;         /* frames whose magic did not match */
	u64  seq_mismatch;       /* wm.frame_seq != mailbox ep_frame_seq */
	u64  ch_mismatch;        /* wm.ch_index != this channel index */
	u64  inversions;         /* wm.frame_seq <= previous delivered seq */
	u64  ts_inversions;      /* wm.capture_ktime_ns < previous (time went back) */
	u64  max_backward_jump;  /* largest (prev_seq - wm.frame_seq) seen */
	u32  last_wm_seq;        /* last decoded watermark sequence */
	u64  last_wm_ktime;      /* last decoded capture timestamp (ns) */
	bool have_last;          /* last_wm_seq / last_wm_ktime are valid */
};

/*
 * Per-channel state
 */
struct mdb5_channel {
	struct mdb5_dev        *mdev;
	unsigned int            index;

	/* V4L2 */
	struct v4l2_device      v4l2_dev;
	struct video_device     vdev;
	struct vb2_queue        vb2_q;
	struct mutex            vb2_lock;
	struct v4l2_pix_format  pix_fmt;
	u32                     sequence;
	bool                    streaming;

	/* Buffer management */
	struct list_head        buf_list;  /* vb2 buffers queued and waiting */
	spinlock_t              buf_lock;

	/* BRAM mailbox pointer for this channel */
	void __iomem           *mbox;

	/* DMA — uses mdev->dma_chan (shared, serialized by mdev->dma_lock) */

	/* Frame state from ISR (set in primary, consumed in threaded) */
	u64                     ep_frame_addr;
	u32                     ep_frame_size;
	u32                     ep_frame_seq;

	/* Auto-trigger (debugfs testing) */
	struct task_struct     *auto_trig_task;
	u64                     auto_trig_addr;

	/* In-band watermark decode stats (see wm_decode module param) */
	struct mdb5_wm_stats    wm_stats;
};

/*
 * Top-level device state (one per PCI function)
 */
struct mdb5_dev {
	struct pci_dev         *pdev;
	void __iomem           *bar[MDB5_NUM_BARS];
	void __iomem           *bram;       /* bar[USER_BAR] + MDB5_BRAM_OFFSET */

	/* eDMA (Designware) */
	struct dw_edma_chip     chip;
	int                     edma_nr_irqs;  /* MSI-X vectors used by eDMA */

	/* Shared DMA channel — CPM5 HDMA has only 1 usable read channel */
	struct dma_chan         *dma_chan;
	struct mutex             dma_lock;

	struct dentry          *debugfs_dir;

	unsigned int            num_channels;
	struct mdb5_channel     channels[MDB5_MAX_CHANNELS];
};

/* Module parameters (defined in mdb5_pci.c) */
extern int dma_timeout_ms;
extern bool wm_decode;

/* mdb5_channel.c */
int  mdb5_channel_init(struct mdb5_channel *ch);
void mdb5_channel_fini(struct mdb5_channel *ch);

/* mdb5_dma.c */
int  mdb5_dma_init(struct mdb5_dev *mdev);
void mdb5_dma_fini(struct mdb5_dev *mdev);
void mdb5_handle_frame(struct mdb5_channel *ch);

#endif /* MDB5_INTERNAL_H */
