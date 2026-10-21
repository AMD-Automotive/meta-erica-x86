// SPDX-License-Identifier: GPL-2.0-only
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: BW
// Maintainer: BW
/*
 * mdb5_pci.c - PCI probe/remove for MDB5 V4L2 video bridge driver
 *
 * Handles:
 *   - PCI device lifecycle (probe, remove)
 *   - BAR mapping
 *   - MSI-X interrupt allocation
 *   - Channel discovery via BRAM mailbox (populated by endpoint proxy)
 *   - Per-channel initialization and teardown
 */

#include <linux/module.h>
#include <linux/pci.h>
#include <linux/delay.h>
#include <linux/interrupt.h>
#include <linux/bitfield.h>
#include <linux/debugfs.h>
#include <linux/kthread.h>
#include <linux/dma/edma.h>

#include "mdb5_bridge.h"
#include "mdb5_internal.h"

#define DRV_NAME	"mdb5_v4l2"
#define DRV_VERSION	"1.0.0"
#define DRV_DESC	"MDB5 PCIe Video Bridge V4L2 Driver"

MODULE_AUTHOR("Advanced Micro Devices, Inc.");
MODULE_DESCRIPTION(DRV_DESC);
MODULE_VERSION(DRV_VERSION);
MODULE_LICENSE("GPL v2");

static int discovery_timeout_s = 10;
module_param(discovery_timeout_s, int, 0444);
MODULE_PARM_DESC(discovery_timeout_s,
		 "Timeout in seconds waiting for endpoint proxy (default 10)");

int dma_timeout_ms = 100;
module_param(dma_timeout_ms, int, 0644);
MODULE_PARM_DESC(dma_timeout_ms,
		 "DMA completion timeout in milliseconds (default 100)");

static bool skip_ep_wait;
module_param(skip_ep_wait, bool, 0444);
MODULE_PARM_DESC(skip_ep_wait,
		 "Skip endpoint proxy wait and seed BRAM with test config (default false)");

bool wm_decode = true;
module_param(wm_decode, bool, 0644);
MODULE_PARM_DESC(wm_decode,
		 "Decode the endpoint in-band frame watermark: set vb.sequence and "
		 "vb.timestamp from it and maintain per-channel stats (default true)");

static const struct pci_device_id mdb5_pci_ids[] = {
	{ PCI_DEVICE(MDB5_VENDOR_ID, MDB5_DEVICE_ID) },
	{ 0 }
};
MODULE_DEVICE_TABLE(pci, mdb5_pci_ids);

/* ---- VSEC-based eDMA discovery ---- */

#define DW_PCIE_VSEC_DMA_ID	0x6
#define DW_PCIE_VSEC_DMA_BAR	GENMASK(10, 8)
#define DW_PCIE_VSEC_DMA_MAP	GENMASK(2, 0)
#define DW_PCIE_VSEC_DMA_WR_CH	GENMASK(9, 0)
#define DW_PCIE_VSEC_DMA_RD_CH	GENMASK(25, 16)

static bool mdb5_vsec_discover(struct pci_dev *pdev,
			       int *out_bar, u32 *out_off,
			       enum dw_edma_map_format *out_mf,
			       u16 *out_wr_cnt, u16 *out_rd_cnt)
{
	u16 vsec;
	u32 val;

	vsec = pci_find_vsec_capability(pdev, 0x16c3, DW_PCIE_VSEC_DMA_ID);
	if (!vsec)
		vsec = pci_find_vsec_capability(pdev, MDB5_VENDOR_ID,
						DW_PCIE_VSEC_DMA_ID);
	if (!vsec) {
		dev_info(&pdev->dev, "No eDMA VSEC capability found\n");
		return false;
	}

	pci_read_config_dword(pdev, vsec + 0x8, &val);
	*out_bar = FIELD_GET(DW_PCIE_VSEC_DMA_BAR, val);
	*out_mf  = FIELD_GET(DW_PCIE_VSEC_DMA_MAP, val);

	pci_read_config_dword(pdev, vsec + 0xc, &val);
	*out_wr_cnt = FIELD_GET(DW_PCIE_VSEC_DMA_WR_CH, val);
	*out_rd_cnt = FIELD_GET(DW_PCIE_VSEC_DMA_RD_CH, val);

	dev_info(&pdev->dev,
		 "VSEC DMA: BAR%d, mf=%u, wr_ch=%u, rd_ch=%u\n",
		 *out_bar, *out_mf, *out_wr_cnt, *out_rd_cnt);
	return true;
}

/* ---- dw_edma_probe() platform ops ---- */

static int mdb5_irq_vector(struct device *dev, unsigned int nr)
{
	return pci_irq_vector(to_pci_dev(dev), nr);
}

/*
 * Identity pci_address callback.
 *
 * dw-edma-core calls this on sg_dma_address() values (host DMA buffers).
 * On x86, DMA addresses from dma_alloc_coherent() / vb2_dma_contig are
 * already PCI bus addresses.  Do NOT use pcibios_resource_to_bus() here —
 * that API is for PCI BAR resources, not system-RAM DMA addresses.
 */
static u64 mdb5_pci_address(struct device *dev, phys_addr_t cpu_addr)
{
	return cpu_addr;
}

static const struct dw_edma_plat_ops mdb5_plat_ops = {
	.irq_vector  = mdb5_irq_vector,
	.pci_address = mdb5_pci_address,
};

/*
 * Initialize the Designware eDMA engine via dw_edma_probe().
 *
 * Must be called after BARs are mapped and MSI-X vectors allocated.
 * Populates mdev->chip and registers eDMA channels with dmaengine.
 */
static int mdb5_edma_init(struct mdb5_dev *mdev)
{
	struct pci_dev *pdev = mdev->pdev;
	struct dw_edma_chip *chip = &mdev->chip;
	struct dw_edma_region *region;
	int edma_bar = MDB5_CONFIG_BAR;
	u32 edma_off = 0;
	enum dw_edma_map_format mf = EDMA_MF_EDMA_UNROLL;
	u16 wr_cnt = 1, rd_cnt = 1;
	phys_addr_t bram_bus;
	int ret;

	/* Discover eDMA config via VSEC capability */
	mdb5_vsec_discover(pdev, &edma_bar, &edma_off, &mf, &wr_cnt, &rd_cnt);

	/*
	 * CPM5 HDMA reports 8 wr + 8 rd channels via VSEC, but only
	 * 1 of each is actually usable.  All video channels share the
	 * single read channel, serialized by mdev->dma_lock.
	 */
	wr_cnt = 1;
	rd_cnt = 1;

	if (!mdev->bar[edma_bar]) {
		dev_err(&pdev->dev, "eDMA BAR%d not mapped\n", edma_bar);
		return -ENODEV;
	}

	/* Fill dw_edma_chip */
	chip->dev      = &pdev->dev;
	chip->mf       = mf;
	chip->nr_irqs  = mdev->edma_nr_irqs;
	chip->ops      = &mdb5_plat_ops;
	chip->flags    = 0; /* non-LOCAL: RIE for MSI-X interrupts to host */
	chip->reg_base = mdev->bar[edma_bar] + edma_off;
	chip->ll_wr_cnt = wr_cnt;
	chip->ll_rd_cnt = rd_cnt;

	/*
	 * LL descriptor paddr must use the PCIe-to-AXI translation address
	 * from the Vivado address editor (0x800_0000_0000 base).  The eDMA
	 * engine fetches descriptors via the CPM5 NoC path, which uses this
	 * address space — NOT the APU physical address.
	 */
	bram_bus = MDB5_EP_BRAM_PCIE_PHYS;

	/* Write channel LL + DT regions (in endpoint BRAM) */
	region = &chip->ll_region_wr[0];
	region->vaddr.io = mdev->bram + MDB5_LL_WR_BRAM_OFFSET;
	region->paddr    = bram_bus + MDB5_LL_WR_BRAM_OFFSET;
	region->sz       = MDB5_LL_WR_BRAM_SIZE;

	region = &chip->dt_region_wr[0];
	region->vaddr.io = mdev->bram + MDB5_DT_WR_BRAM_OFFSET;
	region->paddr    = bram_bus + MDB5_DT_WR_BRAM_OFFSET;
	region->sz       = MDB5_DT_WR_BRAM_SIZE;

	/* Read channel LL + DT regions (single channel, shared by all video channels) */
	region = &chip->ll_region_rd[0];
	region->vaddr.io = mdev->bram + MDB5_LL_RD_BRAM_OFFSET;
	region->paddr    = bram_bus + MDB5_LL_RD_BRAM_OFFSET;
	region->sz       = MDB5_LL_RD_BRAM_SIZE;

	region = &chip->dt_region_rd[0];
	region->vaddr.io = mdev->bram + MDB5_DT_RD_BRAM_OFFSET;
	region->paddr    = bram_bus + MDB5_DT_RD_BRAM_OFFSET;
	region->sz       = MDB5_DT_RD_BRAM_SIZE;

	/* Clear descriptor memory before probe */
	memset_io(mdev->bram + MDB5_LL_WR_BRAM_OFFSET, 0,
		  MDB5_LL_WR_BRAM_SIZE + MDB5_DT_WR_BRAM_SIZE +
		  MDB5_LL_RD_BRAM_SIZE + MDB5_DT_RD_BRAM_SIZE);

	dev_info(&pdev->dev,
		 "eDMA: BAR%d+0x%x mf=%u wr=%u rd=%u irqs=%d\n",
		 edma_bar, edma_off, mf, wr_cnt, rd_cnt, chip->nr_irqs);

	ret = dw_edma_probe(chip);
	if (ret) {
		dev_err(&pdev->dev, "dw_edma_probe failed: %d\n", ret);
		return ret;
	}

	dev_info(&pdev->dev, "dw_edma_probe() succeeded\n");
	return 0;
}

static void mdb5_edma_fini(struct mdb5_dev *mdev)
{
	dw_edma_remove(&mdev->chip);
}

/*
 * Map a single PCIe BAR
 */
static void __iomem *mdb5_map_bar(struct pci_dev *pdev, int bar)
{
	resource_size_t bar_start = pci_resource_start(pdev, bar);
	resource_size_t bar_len = pci_resource_len(pdev, bar);
	void __iomem *addr;

	if (!bar_start || !bar_len) {
		dev_err(&pdev->dev, "BAR %d not available\n", bar);
		return NULL;
	}

	addr = pci_iomap(pdev, bar, bar_len);
	if (!addr) {
		dev_err(&pdev->dev, "Failed to map BAR %d (start=0x%llx len=0x%llx)\n",
			bar, (u64)bar_start, (u64)bar_len);
		return NULL;
	}

	dev_info(&pdev->dev, "BAR %d mapped: phys=0x%llx len=0x%llx virt=%p\n",
		 bar, (u64)bar_start, (u64)bar_len, addr);
	return addr;
}

/*
 * Wait for endpoint proxy to populate BRAM with discovery data.
 * Polls the BRAM header for the MDB5_MAGIC value.
 */
/*
 * Seed BRAM with a single-channel test configuration.
 * Used when skip_ep_wait=1 to bypass the endpoint proxy.
 * Configures 4 channels as 1792x1536 NV12 (256-aligned stride).
 */
static void mdb5_seed_bram(struct mdb5_dev *mdev)
{
	static const struct {
		u32 width, height, fmt, stride;
	} ch_cfg[] = {
		{ 1792, 1536, V4L2_PIX_FMT_NV12, 1792 },
		{ 1792, 1536, V4L2_PIX_FMT_NV12, 1792 },
		{ 1792, 1536, V4L2_PIX_FMT_NV12, 1792 },
		{ 1792, 1536, V4L2_PIX_FMT_NV12, 1792 },
	};
	unsigned int n = ARRAY_SIZE(ch_cfg);
	void __iomem *hdr = mdev->bram;

	iowrite32(MDB5_MAGIC, hdr + offsetof(struct mdb5_mbox_header, magic));
	iowrite32(n,          hdr + offsetof(struct mdb5_mbox_header, num_channels));
	iowrite32(MDB5_EP_STATUS_READY, hdr + offsetof(struct mdb5_mbox_header, ep_status));

	for (unsigned int i = 0; i < n; i++) {
		void __iomem *slot = mdev->bram + MDB5_MBOX_SLOT_OFFSET(i);

		iowrite32(ch_cfg[i].width,  slot + offsetof(struct mdb5_mbox_slot, width));
		iowrite32(ch_cfg[i].height, slot + offsetof(struct mdb5_mbox_slot, height));
		iowrite32(ch_cfg[i].fmt,    slot + offsetof(struct mdb5_mbox_slot, pixel_format));
		iowrite32(ch_cfg[i].stride, slot + offsetof(struct mdb5_mbox_slot, bytesperline));
	}

	dev_info(&mdev->pdev->dev, "BRAM seeded with %u-channel test config\n", n);
}

static int mdb5_wait_for_ep(struct mdb5_dev *mdev)
{
	void __iomem *hdr_base = mdev->bram;
	unsigned long deadline = jiffies + discovery_timeout_s * HZ;
	u32 magic;

	if (skip_ep_wait) {
		dev_info(&mdev->pdev->dev,
			 "Skipping endpoint proxy wait (skip_ep_wait=1)\n");
		mdb5_seed_bram(mdev);
		return 0;
	}

	dev_info(&mdev->pdev->dev,
		 "Waiting for endpoint proxy (timeout %ds)...\n",
		 discovery_timeout_s);

	while (time_before(jiffies, deadline)) {
		magic = ioread32(hdr_base + offsetof(struct mdb5_mbox_header, magic));
		if (magic == MDB5_MAGIC) {
			u32 status = ioread32(hdr_base + offsetof(struct mdb5_mbox_header, ep_status));
			if (status & MDB5_EP_STATUS_READY) {
				dev_info(&mdev->pdev->dev,
					 "Endpoint proxy ready\n");
				return 0;
			}
		}
		msleep(100);
	}

	dev_err(&mdev->pdev->dev,
		"Timeout waiting for endpoint proxy (magic=0x%08x)\n",
		ioread32(hdr_base + offsetof(struct mdb5_mbox_header, magic)));
	return -ETIMEDOUT;
}

/*
 * Discover channels from BRAM mailbox data.
 * The endpoint proxy has already written the header and per-channel
 * format metadata.
 */
static int mdb5_discover_channels(struct mdb5_dev *mdev)
{
	void __iomem *hdr_base = mdev->bram;
	u32 num_ch = ioread32(hdr_base + offsetof(struct mdb5_mbox_header, num_channels));

	if (num_ch == 0 || num_ch > MDB5_MAX_CHANNELS) {
		dev_err(&mdev->pdev->dev,
			"Invalid channel count from BRAM: %u\n", num_ch);
		return -EINVAL;
	}

	mdev->num_channels = num_ch;
	dev_info(&mdev->pdev->dev, "Discovered %u channels\n", num_ch);

	for (unsigned int i = 0; i < num_ch; i++) {
		struct mdb5_channel *ch = &mdev->channels[i];
		void __iomem *slot = mdev->bram + MDB5_MBOX_SLOT_OFFSET(i);

		ch->mdev = mdev;
		ch->index = i;
		ch->mbox = slot;

		/* Read static format fields from BRAM */
		u32 width = ioread32(slot + offsetof(struct mdb5_mbox_slot, width));
		u32 height = ioread32(slot + offsetof(struct mdb5_mbox_slot, height));
		u32 pixfmt = ioread32(slot + offsetof(struct mdb5_mbox_slot, pixel_format));
		u32 stride = ioread32(slot + offsetof(struct mdb5_mbox_slot, bytesperline));

		ch->pix_fmt.width = width;
		ch->pix_fmt.height = height;
		ch->pix_fmt.pixelformat = pixfmt;
		/* Report the endpoint's native stride so the reported layout
		 * matches the frame data the eDMA actually transfers. */
		ch->pix_fmt.bytesperline = stride;
		ch->pix_fmt.sizeimage = mdb5_image_size(pixfmt,
					stride / mdb5_fmt_bpp(pixfmt), height);
		ch->pix_fmt.field = V4L2_FIELD_NONE;
		ch->pix_fmt.colorspace = (mdb5_fmt_bpp(pixfmt) <= 2) ?
			V4L2_COLORSPACE_REC709 : V4L2_COLORSPACE_SRGB;

		dev_info(&mdev->pdev->dev,
			 "Ch%u: %ux%u fmt=0x%08x stride=%u size=%u\n",
			 i, width, height, pixfmt, stride,
			 ch->pix_fmt.sizeimage);
	}

	return 0;
}

/*
 * MSI-X interrupt handler (primary - hardirq context)
 *
 * Reads the BRAM mailbox for this channel to check if a frame is ready.
 * If VALID is set, stashes the endpoint frame address and wakes the
 * threaded handler.
 */
static irqreturn_t mdb5_irq_primary(int irq, void *data)
{
	struct mdb5_channel *ch = data;
	void __iomem *slot = ch->mbox;
	u32 status;

	status = ioread32(slot + offsetof(struct mdb5_mbox_slot, frame_status));
	if (!(status & MDB5_FRAME_VALID))
		return IRQ_NONE;

	/* Read frame address and sequence before waking thread */
	ch->ep_frame_addr =
		((u64)ioread32(slot + offsetof(struct mdb5_mbox_slot, frame_addr_hi)) << 32) |
		ioread32(slot + offsetof(struct mdb5_mbox_slot, frame_addr_lo));
	ch->ep_frame_size = ioread32(slot + offsetof(struct mdb5_mbox_slot, frame_size));
	ch->ep_frame_seq = ioread32(slot + offsetof(struct mdb5_mbox_slot, frame_sequence));

	return IRQ_WAKE_THREAD;
}

/*
 * MSI-X interrupt handler (threaded)
 *
 * Initiates DMA transfer from endpoint DDR to the next available
 * host-side vb2 buffer.
 */
static irqreturn_t mdb5_irq_thread(int irq, void *data)
{
	struct mdb5_channel *ch = data;

	mdb5_handle_frame(ch);

	return IRQ_HANDLED;
}

/*
 * Allocate MSI-X vectors for both eDMA and frame notifications.
 *
 * The eDMA engine uses the first N vectors (wr_done + rd_done = 2).
 * Frame notification vectors follow at offset edma_nr_irqs.
 *
 * Must be called after channel discovery (num_channels is known)
 * and before mdb5_edma_init() (which calls dw_edma_probe).
 */
static int mdb5_alloc_irq_vectors(struct mdb5_dev *mdev)
{
	int total = MDB5_EDMA_IRQS + mdev->num_channels;
	int ret;

	ret = pci_alloc_irq_vectors(mdev->pdev, total, total,
				    PCI_IRQ_MSIX | PCI_IRQ_MSI);
	if (ret < 0) {
		dev_err(&mdev->pdev->dev,
			"Failed to allocate %d MSI-X vectors: %d\n",
			total, ret);
		return ret;
	}

	mdev->edma_nr_irqs = MDB5_EDMA_IRQS;
	dev_info(&mdev->pdev->dev,
		 "Allocated %d MSI-X vectors (%d eDMA + %u frame)\n",
		 ret, MDB5_EDMA_IRQS, mdev->num_channels);
	return 0;
}

/*
 * Set up frame notification IRQs (one per channel).
 * Frame vectors start at offset edma_nr_irqs in the MSI-X table.
 */
static int mdb5_setup_frame_irqs(struct mdb5_dev *mdev)
{
	int ret;

	for (unsigned int i = 0; i < mdev->num_channels; i++) {
		struct mdb5_channel *ch = &mdev->channels[i];
		int vec_idx = mdev->edma_nr_irqs + i;
		int vector = pci_irq_vector(mdev->pdev, vec_idx);

		ret = request_threaded_irq(vector,
					   mdb5_irq_primary,
					   mdb5_irq_thread,
					   0,
					   DRV_NAME,
					   ch);
		if (ret) {
			dev_err(&mdev->pdev->dev,
				"Failed to request IRQ %d for ch%u: %d\n",
				vector, i, ret);
			while (i > 0) {
				i--;
				free_irq(pci_irq_vector(mdev->pdev,
					 mdev->edma_nr_irqs + i),
					 &mdev->channels[i]);
			}
			return ret;
		}

		dev_info(&mdev->pdev->dev,
			 "Ch%u: frame IRQ %d (vec %d) registered\n",
			 i, vector, vec_idx);
	}

	return 0;
}

static void mdb5_teardown_frame_irqs(struct mdb5_dev *mdev)
{
	for (unsigned int i = 0; i < mdev->num_channels; i++) {
		int vec_idx = mdev->edma_nr_irqs + i;
		int vector = pci_irq_vector(mdev->pdev, vec_idx);

		free_irq(vector, &mdev->channels[i]);
	}
}

/* ---- debugfs: manual + auto DMA trigger for testing ---- */

/*
 * Auto-trigger kthread: fires DMA at ~30fps from a fixed EP address.
 * Runs until stopped or streaming ends.
 */
static int mdb5_auto_trigger_fn(void *data)
{
	struct mdb5_channel *ch = data;
	unsigned long interval = msecs_to_jiffies(33); /* ~30fps */

	dev_info(&ch->mdev->pdev->dev,
		 "Ch%u: auto-trigger started (EP 0x%llx, ~30fps)\n",
		 ch->index, ch->auto_trig_addr);

	while (!kthread_should_stop()) {
		if (ch->streaming) {
			ch->ep_frame_addr = ch->auto_trig_addr;
			ch->ep_frame_size = ch->pix_fmt.sizeimage;
			ch->ep_frame_seq++;
			mdb5_handle_frame(ch);
		}
		schedule_timeout_interruptible(interval);
	}

	dev_info(&ch->mdev->pdev->dev,
		 "Ch%u: auto-trigger stopped\n", ch->index);
	return 0;
}

/*
 * Write an EP DDR hex address to trigger a single DMA frame capture.
 * Example: echo 0x60000000 > /sys/kernel/debug/0000:62:00.0/ch0_trigger
 *
 * Requires V4L2 streaming to be active (vb2 buffers queued).
 */
static ssize_t mdb5_debugfs_trigger_write(struct file *file,
					  const char __user *ubuf,
					  size_t count, loff_t *ppos)
{
	struct mdb5_channel *ch = file->private_data;
	char buf[32];
	u64 ep_addr;
	int ret;

	if (count >= sizeof(buf))
		return -EINVAL;

	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	ret = kstrtoull(buf, 0, &ep_addr);
	if (ret)
		return ret;

	if (!ch->streaming) {
		dev_err(&ch->mdev->pdev->dev,
			"Ch%u: not streaming — start v4l2 capture first\n",
			ch->index);
		return -ENOSTR;
	}

	ch->ep_frame_addr = ep_addr;
	ch->ep_frame_size = ch->pix_fmt.sizeimage;
	ch->ep_frame_seq++;

	dev_info(&ch->mdev->pdev->dev,
		 "Ch%u: debugfs trigger DMA from EP 0x%llx (%u bytes)\n",
		 ch->index, ep_addr, ch->ep_frame_size);

	mdb5_handle_frame(ch);

	return count;
}

/*
 * Write EP address to start auto-trigger at ~30fps.
 * Write "0" or "stop" to stop.
 * Example: echo 0x60000000 > /sys/kernel/debug/0000:62:00.0/ch0_auto
 *          echo 0 > /sys/kernel/debug/0000:62:00.0/ch0_auto
 */
static ssize_t mdb5_debugfs_auto_write(struct file *file,
					const char __user *ubuf,
					size_t count, loff_t *ppos)
{
	struct mdb5_channel *ch = file->private_data;
	char buf[32];
	u64 ep_addr;
	int ret;

	if (count >= sizeof(buf))
		return -EINVAL;

	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;
	buf[count] = '\0';

	/* "stop" or "0" → stop auto-trigger */
	if (strncmp(buf, "stop", 4) == 0)
		ep_addr = 0;
	else {
		ret = kstrtoull(buf, 0, &ep_addr);
		if (ret)
			return ret;
	}

	/* Stop existing thread if any */
	if (ch->auto_trig_task) {
		kthread_stop(ch->auto_trig_task);
		ch->auto_trig_task = NULL;
	}

	if (ep_addr == 0)
		return count;

	/* Start auto-trigger thread */
	ch->auto_trig_addr = ep_addr;
	ch->auto_trig_task = kthread_run(mdb5_auto_trigger_fn, ch,
					 "mdb5_auto_%u", ch->index);
	if (IS_ERR(ch->auto_trig_task)) {
		ret = PTR_ERR(ch->auto_trig_task);
		ch->auto_trig_task = NULL;
		dev_err(&ch->mdev->pdev->dev,
			"Ch%u: failed to start auto-trigger: %d\n",
			ch->index, ret);
		return ret;
	}

	return count;
}

static int mdb5_debugfs_open(struct inode *inode, struct file *file)
{
	file->private_data = inode->i_private;
	return 0;
}

/*
 * Read per-channel watermark decode statistics.
 * Populated by mdb5_handle_frame() when wm_decode is enabled.
 */
static ssize_t mdb5_debugfs_wm_stats_read(struct file *file,
					  char __user *ubuf,
					  size_t count, loff_t *ppos)
{
	struct mdb5_channel *ch = file->private_data;
	struct mdb5_wm_stats *s = &ch->wm_stats;
	char buf[512];
	int len;

	len = scnprintf(buf, sizeof(buf),
		"frames %llu\n"
		"wm_valid %llu\n"
		"wm_corrupt %llu\n"
		"seq_mismatch %llu\n"
		"ch_mismatch %llu\n"
		"inversions %llu\n"
		"ts_inversions %llu\n"
		"max_backward_jump %llu\n"
		"last_wm_seq %u\n"
		"last_wm_ktime %llu\n",
		s->frames, s->wm_valid, s->wm_corrupt,
		s->seq_mismatch, s->ch_mismatch, s->inversions,
		s->ts_inversions, s->max_backward_jump,
		s->have_last ? s->last_wm_seq : 0,
		s->have_last ? s->last_wm_ktime : 0);

	return simple_read_from_buffer(ubuf, count, ppos, buf, len);
}

static const struct file_operations mdb5_debugfs_wm_stats_fops = {
	.owner = THIS_MODULE,
	.open  = mdb5_debugfs_open,
	.read  = mdb5_debugfs_wm_stats_read,
};

static const struct file_operations mdb5_debugfs_trigger_fops = {
	.owner = THIS_MODULE,
	.open  = mdb5_debugfs_open,
	.write = mdb5_debugfs_trigger_write,
};

static const struct file_operations mdb5_debugfs_auto_fops = {
	.owner = THIS_MODULE,
	.open  = mdb5_debugfs_open,
	.write = mdb5_debugfs_auto_write,
};

static void mdb5_debugfs_init(struct mdb5_dev *mdev)
{
	struct dentry *dir;
	char name[32];

	dir = debugfs_create_dir(pci_name(mdev->pdev), NULL);
	mdev->debugfs_dir = dir;

	for (unsigned int i = 0; i < mdev->num_channels; i++) {
		snprintf(name, sizeof(name), "ch%u_trigger", i);
		debugfs_create_file(name, 0200, dir,
				    &mdev->channels[i],
				    &mdb5_debugfs_trigger_fops);

		snprintf(name, sizeof(name), "ch%u_auto", i);
		debugfs_create_file(name, 0200, dir,
				    &mdev->channels[i],
				    &mdb5_debugfs_auto_fops);

		snprintf(name, sizeof(name), "ch%u_wm_stats", i);
		debugfs_create_file(name, 0444, dir,
				    &mdev->channels[i],
				    &mdb5_debugfs_wm_stats_fops);
	}
}

static void mdb5_debugfs_fini(struct mdb5_dev *mdev)
{
	/* Stop any auto-trigger threads */
	for (unsigned int i = 0; i < mdev->num_channels; i++) {
		if (mdev->channels[i].auto_trig_task) {
			kthread_stop(mdev->channels[i].auto_trig_task);
			mdev->channels[i].auto_trig_task = NULL;
		}
	}
	debugfs_remove_recursive(mdev->debugfs_dir);
}

/*
 * PCI probe
 */
static int mdb5_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
	struct mdb5_dev *mdev;
	int i, ret;

	dev_info(&pdev->dev, "%s v%s probing\n", DRV_DESC, DRV_VERSION);

	mdev = devm_kzalloc(&pdev->dev, sizeof(*mdev), GFP_KERNEL);
	if (!mdev)
		return -ENOMEM;

	mdev->pdev = pdev;
	pci_set_drvdata(pdev, mdev);

	/* Enable the device */
	ret = pci_enable_device(pdev);
	if (ret) {
		dev_err(&pdev->dev, "pci_enable_device failed: %d\n", ret);
		return ret;
	}

	pci_set_master(pdev);

	/* Set DMA mask */
	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
	if (ret) {
		dev_warn(&pdev->dev, "64-bit DMA mask failed, trying 32-bit\n");
		ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
		if (ret) {
			dev_err(&pdev->dev, "DMA mask setup failed: %d\n", ret);
			goto err_disable;
		}
	}

	/*
	 * Permit large DMA scatter-gather segments. The DMA mapping layer
	 * splits any mapping into chunks no larger than the device's max
	 * segment size (default 64KB). When importing a GPU dumb buffer via
	 * DMABUF, its few large physical chunks (e.g. 4MB + 512KB) would be
	 * fractured into dozens of segments, overflowing the eDMA's 32-entry
	 * linked-list descriptor region in BRAM. Raising the limit keeps the
	 * imported buffer at its natural (small) segment count.
	 */
	dma_set_max_seg_size(&pdev->dev, UINT_MAX);

	/* Request PCI regions */
	ret = pci_request_regions(pdev, DRV_NAME);
	if (ret) {
		dev_err(&pdev->dev, "pci_request_regions failed: %d\n", ret);
		goto err_disable;
	}

	/* Map all available BARs (skip those with size 0) */
	for (i = 0; i < MDB5_NUM_BARS; i++) {
		resource_size_t len = pci_resource_len(pdev, i);

		if (!len)
			continue;
		mdev->bar[i] = mdb5_map_bar(pdev, i);
	}

	if (!mdev->bar[MDB5_CONFIG_BAR]) {
		dev_err(&pdev->dev, "BAR%d (config) not available\n",
			MDB5_CONFIG_BAR);
		ret = -ENODEV;
		goto err_unmap;
	}

	if (!mdev->bar[MDB5_USER_BAR]) {
		dev_err(&pdev->dev, "BAR%d (user/BRAM) not available\n",
			MDB5_USER_BAR);
		ret = -ENODEV;
		goto err_unmap;
	}

	/* Set up BRAM pointer */
	mdev->bram = mdev->bar[MDB5_USER_BAR] + MDB5_BRAM_OFFSET;

	/* Wait for endpoint proxy to initialize BRAM */
	ret = mdb5_wait_for_ep(mdev);
	if (ret)
		goto err_unmap;

	/* Discover channels from BRAM */
	ret = mdb5_discover_channels(mdev);
	if (ret)
		goto err_unmap;

	/* Allocate MSI-X vectors for eDMA + frame notifications */
	ret = mdb5_alloc_irq_vectors(mdev);
	if (ret)
		goto err_unmap;

	/* Initialize eDMA engine (calls dw_edma_probe) */
	ret = mdb5_edma_init(mdev);
	if (ret)
		goto err_irq;

	/* Acquire shared DMA read channel */
	ret = mdb5_dma_init(mdev);
	if (ret)
		dev_warn(&pdev->dev,
			 "DMA init failed (%d), continuing without DMA\n", ret);

	/* Set up per-channel frame notification IRQs */
	ret = mdb5_setup_frame_irqs(mdev);
	if (ret)
		goto err_dma;

	/* Initialize V4L2 devices for each channel */
	for (i = 0; i < (int)mdev->num_channels; i++) {
		ret = mdb5_channel_init(&mdev->channels[i]);
		if (ret) {
			dev_err(&pdev->dev,
				"Ch%u: init failed: %d\n", i, ret);
			while (i > 0) {
				i--;
				mdb5_channel_fini(&mdev->channels[i]);
			}
			goto err_frame_irqs;
		}
	}

	mdb5_debugfs_init(mdev);

	dev_info(&pdev->dev, "%s ready with %u channels\n",
		 DRV_DESC, mdev->num_channels);
	return 0;

err_frame_irqs:
	mdb5_teardown_frame_irqs(mdev);
err_dma:
	mdb5_dma_fini(mdev);
err_edma:
	mdb5_edma_fini(mdev);
err_irq:
	pci_free_irq_vectors(pdev);
err_unmap:
	for (i = 0; i < MDB5_NUM_BARS; i++) {
		if (mdev->bar[i])
			pci_iounmap(pdev, mdev->bar[i]);
	}
	pci_release_regions(pdev);
err_disable:
	pci_disable_device(pdev);
	return ret;
}

/*
 * PCI remove
 */
static void mdb5_remove(struct pci_dev *pdev)
{
	struct mdb5_dev *mdev = pci_get_drvdata(pdev);
	int i;

	dev_info(&pdev->dev, "Removing %s\n", DRV_DESC);

	mdb5_debugfs_fini(mdev);

	for (i = mdev->num_channels - 1; i >= 0; i--)
		mdb5_channel_fini(&mdev->channels[i]);

	mdb5_teardown_frame_irqs(mdev);
	mdb5_dma_fini(mdev);
	mdb5_edma_fini(mdev);
	pci_free_irq_vectors(pdev);

	for (i = 0; i < MDB5_NUM_BARS; i++) {
		if (mdev->bar[i])
			pci_iounmap(pdev, mdev->bar[i]);
	}
	pci_release_regions(pdev);
	pci_disable_device(pdev);
}

static struct pci_driver mdb5_pci_driver = {
	.name     = DRV_NAME,
	.id_table = mdb5_pci_ids,
	.probe    = mdb5_probe,
	.remove   = mdb5_remove,
};

module_pci_driver(mdb5_pci_driver);
