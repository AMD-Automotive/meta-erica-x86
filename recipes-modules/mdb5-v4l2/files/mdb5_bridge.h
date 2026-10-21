/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
/* Contributors: BW
/* Maintainer: BW
/*
 * mdb5_bridge.h - Shared definitions for MDB5 PCIe video bridge
 *
 * Defines the BRAM mailbox protocol between the endpoint proxy
 * (running on Versal APU) and the host V4L2 driver (running on x86).
 *
 * The shared BRAM is accessible from:
 *   - Endpoint side: physical address 0x0800_0000_8000 (via /dev/mem)
 *   - Host side: PCIe BAR2 offset 0x8000
 * Total BRAM size: 8KB (0x2000)
 */
#ifndef MDB5_BRIDGE_H
#define MDB5_BRIDGE_H

#ifdef __KERNEL__
#include <linux/types.h>
#include <linux/videodev2.h>
#else
#include <stdint.h>
typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

/*
 * PCI device identification
 */
#define MDB5_VENDOR_ID          0x10ee
#define MDB5_DEVICE_ID          0x9038

/*
 * PCIe BAR layout
 *
 * BAR0: 1MB — eDMA / config registers
 * BAR1: not present (size 0)
 * BAR2: 512KB — user registers + BRAM (at +0x8000)
 * BAR3: 64KB
 * BAR4: 4KB
 * BAR5: 64KB
 */
#define MDB5_NUM_BARS           6
#define MDB5_CONFIG_BAR         0       /* DMA / eDMA config registers */
#define MDB5_USER_BAR           2       /* BRAM + user registers */

/*
 * BRAM location within BAR1
 */
#define MDB5_BRAM_OFFSET        0x8000
#define MDB5_BRAM_SIZE          0x2000  /* 8KB */

/*
 * eDMA linked-list descriptor regions within BRAM.
 *
 * The upper 4KB of the 8KB BRAM (0x1000-0x1FFF) is carved into
 * LL and DT (data) regions for the eDMA write and read channels.
 * The lower 4KB (0x0000-0x0FFF) holds the mailbox protocol.
 */
#define MDB5_LL_WR_BRAM_OFFSET  0x1000  /* Write channel LL descriptors */
#define MDB5_LL_WR_BRAM_SIZE    0x0400  /* 1KB */
#define MDB5_DT_WR_BRAM_OFFSET  0x1400  /* Write channel data region */
#define MDB5_DT_WR_BRAM_SIZE    0x0400  /* 1KB */

#define MDB5_LL_RD_BRAM_OFFSET  0x1800  /* Read channel LL descriptors */
#define MDB5_LL_RD_BRAM_SIZE    0x0400  /* 1KB */
#define MDB5_DT_RD_BRAM_OFFSET  0x1C00  /* Read channel data region */
#define MDB5_DT_RD_BRAM_SIZE    0x0400  /* 1KB */

/*
 * BRAM endpoint-side physical addresses.
 *
 * APU address: used by ep_proxy for /dev/mem mapping.
 * PCIe/eDMA address: used for eDMA linked-list descriptor paddr.
 *   This is the PCIe-to-AXI translation base from Vivado address editor
 *   plus the BRAM offset within the BAR.
 */
#define MDB5_EP_BRAM_PHYS       0x080000008000ULL /* APU view (NoC path) */
#define MDB5_EP_BRAM_PCIE_PHYS  0x80000008000ULL  /* CPM5 eDMA / PCIe view */

/*
 * MSI-X trigger register (endpoint-side physical address)
 * Write the binary-encoded channel index here to fire MSI-X.
 */
#define MDB5_EP_MSIX_TRIGGER_PHYS 0xED800948ULL

/*
 * eDMA interrupt vectors (wr_done + rd_done).
 * MSI-X vector layout: [0..MDB5_EDMA_IRQS-1] = eDMA,
 *                      [MDB5_EDMA_IRQS..N]    = frame notifications
 */
#define MDB5_EDMA_IRQS          2

/*
 * Maximum number of video channels
 */
#define MDB5_MAX_CHANNELS       8

/*
 * -------------------------------------------------------------------
 * BRAM Mailbox Layout
 * -------------------------------------------------------------------
 *
 * Offset 0x0000: Header (64 bytes) - global discovery
 * Offset 0x0040: Channel 0 slot (64 bytes)
 * Offset 0x0080: Channel 1 slot (64 bytes)
 * ...
 * Offset 0x0040 + N*0x40: Channel N slot
 *
 * The remaining BRAM space is reserved for future use.
 * -------------------------------------------------------------------
 */

#define MDB5_MBOX_HEADER_OFFSET 0x0000
#define MDB5_MBOX_HEADER_SIZE   0x0040  /* 64 bytes */
#define MDB5_MBOX_SLOT_SIZE     0x0040  /* 64 bytes per channel */
#define MDB5_MBOX_SLOT_BASE     0x0040  /* first channel slot offset */

#define MDB5_MBOX_SLOT_OFFSET(ch) \
	(MDB5_MBOX_SLOT_BASE + (ch) * MDB5_MBOX_SLOT_SIZE)

/*
 * Header magic value: ASCII "MDB5" = 0x4D444235
 * Written by endpoint proxy when it is initialized and ready.
 */
#define MDB5_MAGIC              0x4D444235U

/*
 * -------------------------------------------------------------------
 * BRAM Header (offset 0x0000, 64 bytes)
 * Written by endpoint proxy at startup.
 * Read by host driver during probe to discover channels.
 * -------------------------------------------------------------------
 */
struct mdb5_mbox_header {
	u32 magic;              /* 0x00: MDB5_MAGIC when EP proxy is ready */
	u32 num_channels;       /* 0x04: number of active channels */
	u32 ep_status;          /* 0x08: endpoint status flags */
	u32 reserved[13];       /* 0x0C-0x3F: pad to 64 bytes */
} __attribute__((packed));

/* ep_status bits */
#define MDB5_EP_STATUS_READY    (1U << 0)
#define MDB5_EP_STATUS_ERROR    (1U << 1)

/*
 * -------------------------------------------------------------------
 * Per-channel Mailbox Slot (64 bytes each)
 *
 * The "static" fields (width, height, pixel_format, bytesperline)
 * are written once at startup by the endpoint proxy.
 *
 * The "dynamic" fields (frame_addr, frame_size, frame_sequence,
 * frame_status) are updated per-frame by the endpoint proxy.
 *
 * host_ack is written by the host driver after DMA completion.
 * -------------------------------------------------------------------
 */
struct mdb5_mbox_slot {
	/* Dynamic fields - updated per frame by endpoint */
	u32 frame_addr_lo;      /* 0x00: low 32 bits of EP DDR frame addr */
	u32 frame_addr_hi;      /* 0x04: high 32 bits */
	u32 frame_size;         /* 0x08: frame size in bytes */
	u32 frame_sequence;     /* 0x0C: monotonically increasing counter */
	u32 frame_status;       /* 0x10: status flags (VALID, STREAMING) */

	/* Written by host after DMA completes */
	u32 host_ack;           /* 0x14: last completed frame_sequence */

	/* Static fields - written once at startup by endpoint */
	u32 width;              /* 0x18: frame width in pixels */
	u32 height;             /* 0x1C: frame height in pixels */
	u32 pixel_format;       /* 0x20: V4L2 fourcc pixel format */
	u32 bytesperline;       /* 0x24: stride in bytes */

	u32 reserved[6];        /* 0x28-0x3F: pad to 64 bytes */
} __attribute__((packed));

/* frame_status bits */
#define MDB5_FRAME_VALID        (1U << 0)  /* Frame data is ready for DMA */
#define MDB5_FRAME_STREAMING    (1U << 1)  /* Channel is actively streaming */

/*
 * -------------------------------------------------------------------
 * In-band frame watermark
 *
 * Optionally stamped by the endpoint proxy into the FIRST bytes of each
 * captured frame buffer (offset 0), after DQBUF and before raising
 * VALID.  Because it lives in the frame payload, it travels through the
 * exact same eDMA path as the pixels: the host driver decodes it from
 * the DMA'd buffer and can compare the watermark's frame_seq (what the
 * pixels actually are) against the mailbox frame_sequence (what the
 * metadata claimed).  A mismatch is direct proof the frame buffer was
 * reused under the host before the DMA ran.
 *
 * NOTE: stamping overwrites the first sizeof(struct mdb5_frame_wm)
 * bytes (top-left of the image).  Intended for order-integrity testing
 * and frame provenance, not for pixel-exact golden comparison.
 * -------------------------------------------------------------------
 */
#define MDB5_FRAME_WM_MAGIC     0x4D445746U  /* ASCII "MDWF" */

struct mdb5_frame_wm {
	u32 magic;              /* 0x00: MDB5_FRAME_WM_MAGIC when valid */
	u32 ch_index;           /* 0x04: endpoint channel index (catch swaps) */
	u32 frame_seq;          /* 0x08: endpoint monotonic frame sequence */
	u32 reserved;           /* 0x0C: pad */
	u64 capture_ktime_ns;   /* 0x10: endpoint CLOCK_MONOTONIC at DQBUF */
} __attribute__((packed));     /* 24 bytes */

/*
 * -------------------------------------------------------------------
 * Pixel format string-to-fourcc mapping (for JSON config parsing)
 * -------------------------------------------------------------------
 */
#ifndef __KERNEL__
#include <linux/videodev2.h>
#endif

/*
 * Supported pixel formats and their V4L2 fourcc codes.
 * The format string in the JSON config should match one of these.
 */
#define MDB5_FMT_RGB24_STR      "RGB24"
#define MDB5_FMT_BGR24_STR      "BGR24"
#define MDB5_FMT_RGBX32_STR     "RGBX32"
#define MDB5_FMT_BGRX32_STR     "BGRX32"
#define MDB5_FMT_YUYV_STR       "YUYV"
#define MDB5_FMT_UYVY_STR       "UYVY"
#define MDB5_FMT_NV12_STR       "NV12"

/*
 * Helper: bytes per pixel for stride calculation.
 * NV12 is special (1 byte for Y plane, 0.5 for UV), so we report
 * the Y-plane bytesperline = width.
 */
static inline u32 mdb5_fmt_bpp(u32 fourcc)
{
	switch (fourcc) {
#ifdef __KERNEL__
	case V4L2_PIX_FMT_RGB24:
	case V4L2_PIX_FMT_BGR24:
		return 3;
	case V4L2_PIX_FMT_RGBX32:
	case V4L2_PIX_FMT_BGRX32:
		return 4;
	case V4L2_PIX_FMT_YUYV:
	case V4L2_PIX_FMT_UYVY:
		return 2;
	case V4L2_PIX_FMT_NV12:
		return 1; /* Y-plane only; total size = w*h*3/2 */
#else
	case V4L2_PIX_FMT_RGB24:
	case V4L2_PIX_FMT_BGR24:
		return 3;
	case V4L2_PIX_FMT_RGBX32:
	case V4L2_PIX_FMT_BGRX32:
	case V4L2_PIX_FMT_XRGB32:
	case V4L2_PIX_FMT_XBGR32:
		return 4;
	case V4L2_PIX_FMT_YUYV:
	case V4L2_PIX_FMT_UYVY:
	case V4L2_PIX_FMT_YVYU:
		return 2;
	case V4L2_PIX_FMT_NV12:
	case V4L2_PIX_FMT_NV21:
		return 1;
#endif
	default:
		return 3;
	}
}

/*
 * Helper: compute total image size in bytes
 */
static inline u32 mdb5_image_size(u32 fourcc, u32 width, u32 height)
{
	u32 bpp = mdb5_fmt_bpp(fourcc);

	if (fourcc == V4L2_PIX_FMT_NV12 || fourcc == V4L2_PIX_FMT_NV21)
		return width * height * 3 / 2;
	return width * height * bpp;
}

#endif /* MDB5_BRIDGE_H */
