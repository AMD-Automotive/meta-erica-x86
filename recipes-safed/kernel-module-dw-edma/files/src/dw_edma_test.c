// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Synopsys DesignWare eDMA test client
 *
 * Bound to a DT consumer node ("amd,dw-edma-test") that references a
 * dw-edma controller via standard OF DMA bindings. Runs a simple
 * loopback test: M2D writes a pattern to a remote PCIe address, then
 * D2M reads it back and the buffers are compared.
 *
 * Example DT:
 *
 *   edma: dma-controller {
 *       compatible = "amd,dw-edma";
 *       #dma-cells = <1>;
 *   };
 *
 *   dw-edma-test {
 *       compatible = "amd,dw-edma-test";
 *       dmas = <&edma 8>, <&edma 0>;
 *       dma-names = "tx", "rx";
 *   };
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */

#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/dmaengine.h>
#include <linux/init.h>
#include <linux/ktime.h>
#include <linux/mm.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/printk.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>
#include <linux/vmalloc.h>

#define DRV_NAME "dw-edma-test"
#define DW_EDMA_XFER_TIMEOUT_MS 3000
#define DW_EDMA_MB		(1u << 20)

static unsigned long long dev_addr;
static unsigned int xfer_size = 4096;
static unsigned int chunk_mb;

module_param_named(dev_addr, dev_addr, ullong, 0444);
MODULE_PARM_DESC(dev_addr,
		 "Remote PCIe address used as the device side of the xfer");
module_param_named(size, xfer_size, uint, 0444);
MODULE_PARM_DESC(size, "Transfer size in bytes (default 4096)");
module_param_named(chunk_mb, chunk_mb, uint, 0444);
MODULE_PARM_DESC(chunk_mb,
		 "Per-sg-entry chunk size in MiB; 0 = default kernel-allocated sgl, one sg entry per page (default 0)");

struct dw_edma_test {
	struct device	*dev;
	struct dma_chan	*tx_chan;	/* MEM_TO_DEV */
	struct dma_chan	*rx_chan;	/* DEV_TO_MEM */
};

struct xfer_ctx {
	struct completion		 done;
	enum dmaengine_tx_result	 result;
	ktime_t				 t_done;
};

static void dump_chan_info(struct device *dev, const char *tag,
			   struct dma_chan *chan)
{
	struct dma_device *dd = chan->device;
	struct device *ddev = dmaengine_get_dma_device(chan);

	dev_info(dev, "%s channel '%s' (chan_id=%d, dev=%s)\n",
		 tag, dma_chan_name(chan), chan->chan_id,
		 ddev ? dev_name(ddev) : "(null)");
	dev_info(dev, "  cap_mask:        0x%lx\n", dd->cap_mask.bits[0]);
	dev_info(dev, "  src_addr_widths: 0x%x\n", dd->src_addr_widths);
	dev_info(dev, "  dst_addr_widths: 0x%x\n", dd->dst_addr_widths);
}

/*
 * Probe the per-channel slave capabilities to learn which transfer
 * direction the channel supports. dw-edma populates ->device_caps with
 * a single direction bit per channel.
 */
static enum dma_transfer_direction chan_direction(struct dma_chan *chan)
{
	struct dma_slave_caps caps = {};

	if (dma_get_slave_caps(chan, &caps))
		return DMA_TRANS_NONE;

	if (caps.directions & BIT(DMA_MEM_TO_DEV))
		return DMA_MEM_TO_DEV;
	if (caps.directions & BIT(DMA_DEV_TO_MEM))
		return DMA_DEV_TO_MEM;
	return DMA_TRANS_NONE;
}

static void xfer_done_cb(void *param,
			 const struct dmaengine_result *result)
{
	struct xfer_ctx *ctx = param;

	ctx->t_done = ktime_get();
	ctx->result = result->result;
	complete(&ctx->done);
}

static int do_xfer(struct device *dev, struct dma_chan *chan,
		   struct scatterlist *sgl, unsigned int sg_nents,
		   enum dma_transfer_direction dir, s64 *us_out)
{
	struct dma_slave_config cfg = {};
	struct dma_async_tx_descriptor *desc;
	struct xfer_ctx ctx;
	dma_cookie_t cookie;
	unsigned long timeout = msecs_to_jiffies(DW_EDMA_XFER_TIMEOUT_MS);
	ktime_t t_start;
	int ret;

	if (dir == DMA_MEM_TO_DEV)
		cfg.dst_addr = dev_addr;
	else
		cfg.src_addr = dev_addr;

	ret = dmaengine_slave_config(chan, &cfg);
	if (ret) {
		dev_err(dev, "slave_config failed: %d\n", ret);
		return ret;
	}

	desc = dmaengine_prep_slave_sg(chan, sgl, sg_nents, dir,
				       DMA_PREP_INTERRUPT);
	if (!desc) {
		dev_err(dev, "prep_slave_sg failed\n");
		return -EINVAL;
	}

	init_completion(&ctx.done);
	ctx.result = DMA_TRANS_ABORTED;
	desc->callback_result = xfer_done_cb;
	desc->callback_param = &ctx;

	cookie = dmaengine_submit(desc);
	ret = dma_submit_error(cookie);
	if (ret) {
		dev_err(dev, "submit failed: %d\n", ret);
		return ret;
	}

	t_start = ktime_get();
	dma_async_issue_pending(chan);

	if (!wait_for_completion_timeout(&ctx.done, timeout)) {
		dev_err(dev, "xfer (dir=%d) timed out\n", dir);
		dmaengine_terminate_sync(chan);
		return -ETIMEDOUT;
	}
	if (us_out)
		*us_out = ktime_to_ns(ktime_sub(ctx.t_done, t_start)) / 1000;
	if (ctx.result != DMA_TRANS_NOERROR) {
		dev_err(dev, "xfer (dir=%d) failed, result=%d\n",
			dir, ctx.result);
		dmaengine_terminate_sync(chan);
		return -EIO;
	}
	return 0;
}

static void pattern_fill(u8 *buf, size_t size)
{
	size_t i;

	for (i = 0; i < size; i++)
		buf[i] = (u8)(i ^ (i >> 8));
}

/*
 * A test buffer exposed via a scatterlist.
 *
 * Two backing modes:
 *   - chunk_mb == 0: a single vmalloc()'ed buffer; the sgl has one
 *     entry per page (whatever the kernel allocator handed us).
 *   - chunk_mb  > 0: an array of physically-contiguous kmalloc()'ed
 *     chunks of @chunk_mb MiB each, one sg entry per chunk.
 *
 * @vbuf is set in vmalloc mode (linear VA, used by fill/compare/dump).
 * @chunks is set in chunked mode.
 */
struct test_buf {
	struct scatterlist	*sgl;
	unsigned int		 nents;
	size_t			 size;
	void			*vbuf;
	void		       **chunks;
};

static void tbuf_free(struct test_buf *tb)
{
	unsigned int i;

	if (tb->chunks) {
		for (i = 0; i < tb->nents; i++)
			kfree(tb->chunks[i]);
		kfree(tb->chunks);
	}
	vfree(tb->vbuf);
	kfree(tb->sgl);
	memset(tb, 0, sizeof(*tb));
}

static int tbuf_alloc(struct test_buf *tb, size_t size)
{
	unsigned int n, i = 0;
	size_t remaining = size;

	memset(tb, 0, sizeof(*tb));
	tb->size = size;

	if (!chunk_mb) {
		unsigned long off;

		tb->vbuf = vmalloc(size);
		if (!tb->vbuf)
			return -ENOMEM;

		n = DIV_ROUND_UP(size + offset_in_page(tb->vbuf), PAGE_SIZE);
		tb->sgl = kcalloc(n, sizeof(*tb->sgl), GFP_KERNEL);
		if (!tb->sgl)
			goto err;

		sg_init_table(tb->sgl, n);
		off = 0;
		for (i = 0; i < n; i++) {
			unsigned int page_off =
				offset_in_page((unsigned long)tb->vbuf + off);
			size_t cs = min_t(size_t, remaining,
					  PAGE_SIZE - page_off);
			struct page *pg =
				vmalloc_to_page(tb->vbuf + off);

			if (!pg)
				goto err;
			sg_set_page(&tb->sgl[i], pg, cs, page_off);
			remaining -= cs;
			off += cs;
		}
		tb->nents = n;
		return 0;
	}

	n = DIV_ROUND_UP(size, (size_t)chunk_mb * DW_EDMA_MB);
	tb->chunks = kcalloc(n, sizeof(*tb->chunks), GFP_KERNEL);
	tb->sgl = kcalloc(n, sizeof(*tb->sgl), GFP_KERNEL);
	if (!tb->chunks || !tb->sgl)
		goto err;

	sg_init_table(tb->sgl, n);
	for (i = 0; i < n; i++) {
		size_t cs = min_t(size_t, remaining,
				  (size_t)chunk_mb * DW_EDMA_MB);

		tb->chunks[i] = kmalloc(cs, GFP_KERNEL);
		if (!tb->chunks[i])
			goto err;
		sg_set_buf(&tb->sgl[i], tb->chunks[i], cs);
		remaining -= cs;
	}
	tb->nents = n;
	return 0;
err:
	tb->nents = i;
	tbuf_free(tb);
	return -ENOMEM;
}

static void tbuf_fill_pattern(struct test_buf *tb)
{
	unsigned int i;

	if (tb->vbuf) {
		pattern_fill(tb->vbuf, tb->size);
		return;
	}
	for (i = 0; i < tb->nents; i++)
		pattern_fill(tb->chunks[i], tb->sgl[i].length);
}

/*
 * Compare two test_bufs; returns 0 on match or the byte offset (>=1)
 * of the first mismatch.
 */
static ssize_t tbuf_compare(const struct test_buf *a,
			    const struct test_buf *b)
{
	unsigned int i;
	size_t off = 0;

	if (a->size != b->size)
		return -EINVAL;

	if (a->vbuf && b->vbuf) {
		if (memcmp(a->vbuf, b->vbuf, a->size))
			return 1;
		return 0;
	}

	if (a->nents != b->nents)
		return -EINVAL;

	for (i = 0; i < a->nents; i++) {
		size_t cs = a->sgl[i].length;

		if (cs != b->sgl[i].length)
			return -EINVAL;
		if (memcmp(a->chunks[i], b->chunks[i], cs))
			return off + 1;
		off += cs;
	}
	return 0;
}

static void dump_head(const char *tag, const struct test_buf *tb)
{
	size_t n;
	const void *p;

	if (tb->vbuf) {
		p = tb->vbuf;
		n = min_t(size_t, tb->size, 64);
	} else if (tb->nents) {
		p = tb->chunks[0];
		n = min_t(size_t, tb->sgl[0].length, 64);
	} else {
		return;
	}
	print_hex_dump(KERN_ERR, tag, DUMP_PREFIX_OFFSET, 16, 1, p, n, false);
}

static int dw_edma_run_test(struct dw_edma_test *t)
{
	struct device *dev = t->dev;
	struct device *tx_dev = dmaengine_get_dma_device(t->tx_chan);
	struct device *rx_dev = dmaengine_get_dma_device(t->rx_chan);
	struct test_buf tx = {}, rx = {};
	int tx_mapped = 0, rx_mapped = 0;
	s64 tx_us = 0, rx_us = 0;
	ssize_t cmp;
	int ret;

	ret = tbuf_alloc(&tx, xfer_size);
	if (ret) {
		dev_err(dev, "tx tbuf_alloc failed: %d\n", ret);
		goto out;
	}
	ret = tbuf_alloc(&rx, xfer_size);
	if (ret) {
		dev_err(dev, "rx tbuf_alloc failed: %d\n", ret);
		goto out;
	}
	tbuf_fill_pattern(&tx);

	tx_mapped = dma_map_sg(tx_dev, tx.sgl, tx.nents, DMA_TO_DEVICE);
	if (!tx_mapped) {
		dev_err(dev, "dma_map_sg(tx) failed\n");
		ret = -EIO;
		goto out;
	}

	dev_info(dev, "M2D %u bytes (%u sg ents) -> 0x%llx\n",
		 xfer_size, tx_mapped, dev_addr);
	ret = do_xfer(dev, t->tx_chan, tx.sgl, tx_mapped,
		      DMA_MEM_TO_DEV, &tx_us);
	if (ret) {
		dma_unmap_sg(tx_dev, tx.sgl, tx.nents, DMA_TO_DEVICE);
		tx_mapped = 0;
		goto out;
	}
	dev_info(dev, "M2D run #1 took %lld us\n", tx_us);
	ret = do_xfer(dev, t->tx_chan, tx.sgl, tx_mapped,
		      DMA_MEM_TO_DEV, &tx_us);
	dma_unmap_sg(tx_dev, tx.sgl, tx.nents, DMA_TO_DEVICE);
	tx_mapped = 0;
	if (ret)
		goto out;
	dev_info(dev, "M2D run #2 took %lld us\n", tx_us);

	rx_mapped = dma_map_sg(rx_dev, rx.sgl, rx.nents, DMA_FROM_DEVICE);
	if (!rx_mapped) {
		dev_err(dev, "dma_map_sg(rx) failed\n");
		ret = -EIO;
		goto out;
	}

	dev_info(dev, "D2M %u bytes (%u sg ents) <- 0x%llx\n",
		 xfer_size, rx_mapped, dev_addr);
	ret = do_xfer(dev, t->rx_chan, rx.sgl, rx_mapped,
		      DMA_DEV_TO_MEM, &rx_us);
	if (ret) {
		dma_unmap_sg(rx_dev, rx.sgl, rx.nents, DMA_FROM_DEVICE);
		rx_mapped = 0;
		goto out;
	}
	dev_info(dev, "D2M run #1 took %lld us\n", rx_us);
	ret = do_xfer(dev, t->rx_chan, rx.sgl, rx_mapped,
		      DMA_DEV_TO_MEM, &rx_us);
	dma_unmap_sg(rx_dev, rx.sgl, rx.nents, DMA_FROM_DEVICE);
	rx_mapped = 0;
	if (ret)
		goto out;
	dev_info(dev, "D2M run #2 took %lld us\n", rx_us);

	cmp = tbuf_compare(&tx, &rx);
	if (cmp) {
		dev_err(dev, "data mismatch near offset %zd\n", cmp);
		dump_head("tx: ", &tx);
		dump_head("rx: ", &rx);
		ret = -EIO;
		goto out;
	}

	dev_info(dev, "loopback OK (%u bytes)\n", xfer_size);
	ret = 0;
out:
	if (tx_mapped)
		dma_unmap_sg(tx_dev, tx.sgl, tx.nents, DMA_TO_DEVICE);
	if (rx_mapped)
		dma_unmap_sg(rx_dev, rx.sgl, rx.nents, DMA_FROM_DEVICE);
	tbuf_free(&tx);
	tbuf_free(&rx);
	return ret;
}

static int dw_edma_test_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct dw_edma_test *t;
	int ret;

	if (!xfer_size) {
		dev_err(dev, "size must be > 0\n");
		return -EINVAL;
	}

	t = devm_kzalloc(dev, sizeof(*t), GFP_KERNEL);
	if (!t)
		return -ENOMEM;

	t->dev = dev;
	platform_set_drvdata(pdev, t);

	t->tx_chan = dma_request_chan(dev, "tx");
	if (IS_ERR(t->tx_chan)) {
		ret = PTR_ERR(t->tx_chan);
		t->tx_chan = NULL;
		return dev_err_probe(dev, ret, "failed to get tx channel\n");
	}

	t->rx_chan = dma_request_chan(dev, "rx");
	if (IS_ERR(t->rx_chan)) {
		ret = PTR_ERR(t->rx_chan);
		t->rx_chan = NULL;
		dev_err_probe(dev, ret, "failed to get rx channel\n");
		goto err_release_tx;
	}

	if (chan_direction(t->tx_chan) != DMA_MEM_TO_DEV) {
		dev_err(dev, "tx chan '%s' is not M2D\n",
			dma_chan_name(t->tx_chan));
		ret = -EINVAL;
		goto err_release_rx;
	}
	if (chan_direction(t->rx_chan) != DMA_DEV_TO_MEM) {
		dev_err(dev, "rx chan '%s' is not D2M\n",
			dma_chan_name(t->rx_chan));
		ret = -EINVAL;
		goto err_release_rx;
	}

	dump_chan_info(dev, "M2D", t->tx_chan);
	dump_chan_info(dev, "D2M", t->rx_chan);

	ret = dw_edma_run_test(t);
	if (ret)
		goto err_release_rx;

	return 0;

err_release_rx:
	dma_release_channel(t->rx_chan);
	t->rx_chan = NULL;
err_release_tx:
	dma_release_channel(t->tx_chan);
	t->tx_chan = NULL;
	return ret;
}

static void dw_edma_test_remove(struct platform_device *pdev)
{
	struct dw_edma_test *t = platform_get_drvdata(pdev);

	if (t->rx_chan)
		dma_release_channel(t->rx_chan);
	if (t->tx_chan)
		dma_release_channel(t->tx_chan);
}

static const struct of_device_id dw_edma_test_match[] = {
	{ .compatible = "amd,dw-edma-test" },
	{ }
};
MODULE_DEVICE_TABLE(of, dw_edma_test_match);

static struct platform_driver dw_edma_test_driver = {
	.probe = dw_edma_test_probe,
	.remove = dw_edma_test_remove,
	.driver = {
		.name = DRV_NAME,
		.of_match_table = dw_edma_test_match,
	},
};
module_platform_driver(dw_edma_test_driver);

MODULE_AUTHOR("AMD");
MODULE_DESCRIPTION("Synopsys DesignWare eDMA OF test client");
MODULE_LICENSE("GPL");
// SPDX-License-Identifier: GPL-2.0-or-later
