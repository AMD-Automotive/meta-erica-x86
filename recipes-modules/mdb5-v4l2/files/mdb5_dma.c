// SPDX-License-Identifier: GPL-2.0-only
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: BW
// Maintainer: BW
/*
 * mdb5_dma.c - Designware eDMA client for MDB5 video bridge
 *
 * Uses the upstream dw-edma Linux dmaengine driver to perform
 * card-to-host (DMA_DEV_TO_MEM) transfers of video frames from
 * endpoint DDR to host memory.
 *
 * The CPM5 HDMA advertises 8 read channels via VSEC but only one is
 * actually usable.  All video channels share a single DMA channel,
 * serialized by mdev->dma_lock.  At 4 channels × 30fps × ~7MB the
 * aggregate bandwidth (~840 MB/s) is well within PCIe limits and the
 * per-frame DMA (~1.75ms) leaves headroom in each 33ms frame period.
 */

#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/completion.h>
#include <linux/jiffies.h>
#include <linux/scatterlist.h>

#include "mdb5_internal.h"

/*
 * DMA completion callback.
 */
static void mdb5_dma_callback(void *param)
{
	struct completion *done = param;

	complete(done);
}

/*
 * Filter function for dma_request_channel().
 * Selects a dw-edma channel associated with our PCI device.
 */
static bool mdb5_dma_filter(struct dma_chan *chan, void *param)
{
	struct pci_dev *pdev = param;

	if (chan->device->dev == &pdev->dev)
		return true;

	return false;
}

/*
 * Initialize the shared DMA channel.
 * Called once from PCI probe after dw_edma_probe().
 */
int mdb5_dma_init(struct mdb5_dev *mdev)
{
	dma_cap_mask_t mask;

	mutex_init(&mdev->dma_lock);

	dma_cap_zero(mask);
	dma_cap_set(DMA_SLAVE, mask);

	mdev->dma_chan = dma_request_channel(mask, mdb5_dma_filter, mdev->pdev);
	if (!mdev->dma_chan) {
		dev_warn(&mdev->pdev->dev, "No DMA channel available\n");
		return -ENODEV;
	}

	dev_info(&mdev->pdev->dev, "DMA channel acquired: %s\n",
		 dma_chan_name(mdev->dma_chan));
	return 0;
}

void mdb5_dma_fini(struct mdb5_dev *mdev)
{
	if (mdev->dma_chan) {
		dmaengine_terminate_sync(mdev->dma_chan);
		dma_release_channel(mdev->dma_chan);
		mdev->dma_chan = NULL;
	}
}

/*
 * Perform a card-to-host DMA transfer (serialized).
 *
 * Accepts a scatterlist so the host destination may be physically
 * non-contiguous (e.g. a GPU dumb buffer imported via DMABUF). The
 * eDMA linked-list engine handles up to 32 descriptors from BRAM; the
 * source (endpoint DDR) is read contiguously while the destination is
 * written across each scatterlist segment.
 */
static int mdb5_dma_transfer(struct mdb5_channel *ch,
			     u64 ep_addr, struct scatterlist *sgl,
			     unsigned int sg_nents)
{
	struct mdb5_dev *mdev = ch->mdev;
	struct dma_async_tx_descriptor *desc;
	struct dma_slave_config cfg = {};
	struct completion done;
	dma_cookie_t cookie;
	unsigned long timeout;
	int ret;

	if (!mdev->dma_chan)
		return -ENODEV;

	mutex_lock(&mdev->dma_lock);

	/* Configure: source is endpoint DDR */
	cfg.direction = DMA_DEV_TO_MEM;
	cfg.src_addr = ep_addr;
	cfg.src_addr_width = DMA_SLAVE_BUSWIDTH_4_BYTES;
	cfg.src_maxburst = 16;

	ret = dmaengine_slave_config(mdev->dma_chan, &cfg);
	if (ret) {
		dev_err(&mdev->pdev->dev,
			"Ch%u: dmaengine_slave_config failed: %d\n",
			ch->index, ret);
		goto unlock;
	}

	desc = dmaengine_prep_slave_sg(mdev->dma_chan, sgl, sg_nents,
				       DMA_DEV_TO_MEM,
				       DMA_PREP_INTERRUPT | DMA_CTRL_ACK);
	if (!desc) {
		dev_err(&mdev->pdev->dev,
			"Ch%u: dmaengine_prep_slave_sg failed (nents=%u)\n",
			ch->index, sg_nents);
		ret = -EIO;
		goto unlock;
	}

	init_completion(&done);
	desc->callback = mdb5_dma_callback;
	desc->callback_param = &done;

	cookie = dmaengine_submit(desc);
	if (dma_submit_error(cookie)) {
		dev_err(&mdev->pdev->dev,
			"Ch%u: dmaengine_submit failed\n", ch->index);
		ret = -EIO;
		goto unlock;
	}

	dma_async_issue_pending(mdev->dma_chan);

	/* Wait for completion while holding lock — single HW channel */
	timeout = msecs_to_jiffies(dma_timeout_ms);
	ret = wait_for_completion_timeout(&done, timeout);
	if (ret == 0) {
		dev_err(&mdev->pdev->dev,
			"Ch%u: DMA timeout (%dms)\n",
			ch->index, dma_timeout_ms);
		dmaengine_terminate_sync(mdev->dma_chan);
		ret = -ETIMEDOUT;
		goto unlock;
	}

	mutex_unlock(&mdev->dma_lock);
	return 0;

unlock:
	mutex_unlock(&mdev->dma_lock);
	return ret;
}

/*
 * Decode the endpoint's in-band watermark from the just-DMA'd buffer.
 *
 * The watermark lives in the first bytes of the frame payload, so it
 * travels through the exact same eDMA path as the pixels.  We:
 *   - override vb.sequence / vb.timestamp with the endpoint's ground
 *     truth (so any V4L2 consumer sees true frame order + capture time);
 *   - cross-check the decoded frame_seq against the mailbox ep_frame_seq
 *     (a mismatch is direct evidence the EP reused the frame buffer
 *      before this DMA ran), and the decoded ch_index against ours;
 *   - track per-channel order inversions for debugfs / the soak test.
 *
 * Called only when wm_decode is enabled.
 */
static void mdb5_decode_watermark(struct mdb5_channel *ch,
				  struct vb2_buffer *vb,
				  struct sg_table *sgt)
{
	struct mdb5_wm_stats *s = &ch->wm_stats;
	struct mdb5_frame_wm wm;
	void *va;

	s->frames++;

	va = vb2_plane_vaddr(vb, 0);
	if (!va)
		return;  /* e.g. an imported dmabuf with no kernel mapping */

	/* Make the DMA'd bytes visible to the CPU before reading. */
	dma_sync_sgtable_for_cpu(&ch->mdev->pdev->dev, sgt, DMA_FROM_DEVICE);

	memcpy(&wm, va, sizeof(wm));

	if (wm.magic != MDB5_FRAME_WM_MAGIC) {
		s->wm_corrupt++;
		return;
	}
	s->wm_valid++;

	/* Override V4L2 metadata with the endpoint's ground truth. */
	vb->timestamp = wm.capture_ktime_ns;
	to_vb2_v4l2_buffer(vb)->sequence = wm.frame_seq;

	if (wm.ch_index != ch->index)
		s->ch_mismatch++;

	if (wm.frame_seq != ch->ep_frame_seq) {
		s->seq_mismatch++;
		dev_warn_ratelimited(&ch->mdev->pdev->dev,
			"Ch%u: watermark seq %u != mailbox seq %u (buffer reuse?)\n",
			ch->index, wm.frame_seq, ch->ep_frame_seq);
	}

	/*
	 * Order-integrity checks against the previous frame on this channel.
	 * frame_seq is the primary signal; capture_ktime_ns is a second,
	 * independent witness (the EP's CLOCK_MONOTONIC at DQBUF).  Both are
	 * checked on every frame so that, even in normal field operation, an
	 * occurrence leaves a forensic trail in dmesg + the debugfs counters.
	 *
	 * The dmesg lines are ratelimited (the debugfs counters are the
	 * authoritative totals); a burst is sampled, not fully logged.  The
	 * line carries enough to triage where the reuse happened:
	 *   - wm_seq vs prev / back N      : how far the pixels jumped back
	 *   - ts ... dt                    : the same jump in EP capture time
	 *   - mailbox_seq                  : what the metadata claimed; if it
	 *                                    equals wm_seq the EP stamped the
	 *                                    stale frame, if it is ahead the
	 *                                    buffer was reused before this DMA
	 *   - ep_addr                      : which EP DDR buffer was DMA'd
	 */
	if (s->have_last) {
		bool seq_back = wm.frame_seq <= s->last_wm_seq;
		bool ts_back  = wm.capture_ktime_ns < s->last_wm_ktime;
		s64 dt_ns = (s64)wm.capture_ktime_ns - (s64)s->last_wm_ktime;

		if (seq_back) {
			u64 jump = (u64)s->last_wm_seq - wm.frame_seq;

			s->inversions++;
			if (jump > s->max_backward_jump)
				s->max_backward_jump = jump;
			dev_warn_ratelimited(&ch->mdev->pdev->dev,
				"Ch%u FRAME ORDER INVERSION #%llu: wm_seq %u after %u (back %llu), ts %llu after %llu (dt %lld ns), mailbox_seq %u, ep_addr 0x%llx\n",
				ch->index, s->inversions,
				wm.frame_seq, s->last_wm_seq, jump,
				wm.capture_ktime_ns, s->last_wm_ktime, dt_ns,
				ch->ep_frame_seq,
				(unsigned long long)ch->ep_frame_addr);
		}

		if (ts_back) {
			s->ts_inversions++;
			/*
			 * Time going backward while the sequence did NOT is a
			 * distinct anomaly (e.g. an EP clock glitch) worth its
			 * own line; if seq also inverted the line above already
			 * carries the timestamps.
			 */
			if (!seq_back)
				dev_warn_ratelimited(&ch->mdev->pdev->dev,
					"Ch%u CAPTURE-TIME REGRESSION #%llu w/o seq inversion: ts %llu after %llu (dt %lld ns), wm_seq %u, mailbox_seq %u\n",
					ch->index, s->ts_inversions,
					wm.capture_ktime_ns, s->last_wm_ktime,
					dt_ns, wm.frame_seq, ch->ep_frame_seq);
		}
	}

	s->last_wm_seq = wm.frame_seq;
	s->last_wm_ktime = wm.capture_ktime_ns;
	s->have_last = true;
}

/*
 * Handle a new frame notification from the ISR.
 *
 * Called from the threaded IRQ handler after the primary ISR
 * has read the frame metadata from the BRAM mailbox.
 *
 * Flow:
 *   1. Dequeue a vb2 buffer from the waiting list
 *   2. Initiate DMA from endpoint DDR to host buffer
 *   3. On completion, mark the vb2 buffer done
 *   4. Write host_ack to BRAM so the endpoint can requeue
 */
void mdb5_handle_frame(struct mdb5_channel *ch)
{
	struct mdb5_buffer *buf;
	struct vb2_buffer *vb;
	struct sg_table *sgt;
	unsigned long flags;
	int ret;

	if (!ch->streaming)
		return;

	/* Get the next available vb2 buffer */
	spin_lock_irqsave(&ch->buf_lock, flags);
	if (list_empty(&ch->buf_list)) {
		spin_unlock_irqrestore(&ch->buf_lock, flags);
		dev_dbg(&ch->mdev->pdev->dev,
			"Ch%u: frame dropped (no buffer)\n", ch->index);
		return;
	}
	buf = list_first_entry(&ch->buf_list, struct mdb5_buffer, list);
	list_del(&buf->list);
	spin_unlock_irqrestore(&ch->buf_lock, flags);

	vb = &buf->vb.vb2_buf;

	/* Get the scatter-gather table of the host buffer */
	sgt = vb2_dma_sg_plane_desc(vb, 0);

	/* Perform the DMA transfer */
	ret = mdb5_dma_transfer(ch, ch->ep_frame_addr,
				sgt->sgl, sgt->nents);
	if (ret) {
		dev_err(&ch->mdev->pdev->dev,
			"Ch%u: DMA transfer failed: %d\n", ch->index, ret);
		vb2_buffer_done(vb, VB2_BUF_STATE_ERROR);
		goto ack;
	}

	/* Fill in buffer metadata (host-local defaults) */
	buf->vb.vb2_buf.timestamp = ktime_get_ns();
	buf->vb.sequence = ch->sequence++;
	buf->vb.field = V4L2_FIELD_NONE;

	/* Decode the in-band watermark: overrides sequence/timestamp with the
	 * endpoint's ground truth and records order-integrity stats. */
	if (wm_decode)
		mdb5_decode_watermark(ch, vb, sgt);

	vb2_buffer_done(vb, VB2_BUF_STATE_DONE);

ack:
	/* Write host ACK to BRAM so endpoint can requeue its buffer */
	iowrite32(ch->ep_frame_seq,
		  ch->mbox + offsetof(struct mdb5_mbox_slot, host_ack));
}
