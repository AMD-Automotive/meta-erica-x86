// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * OF dw edma platform driver
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */

#include <linux/dmaengine.h>
#include <linux/init.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_dma.h>
#include <linux/pci.h>
#include <linux/platform_device.h>

#define DRV_NAME "of-dw-edma"
#define EDMA_PCI_VENDOR 0x10ee
#define EDMA_PCI_DEVICE 0x9038

struct of_dw_edma {
	struct device *dev;
	struct pci_dev *pci_dev;
};

struct of_dw_edma_filter {
	struct pci_dev *pci_dev;
	u32 chan_id;
};

static bool of_dw_edma_chan_filter(struct dma_chan *chan, void *param)
{
	struct of_dw_edma_filter *f = param;

	if (!chan->device || !chan->device->dev)
		return false;
	if (chan->device->dev != &f->pci_dev->dev)
		return false;
	return chan->chan_id == f->chan_id;
}

/*
 * DT binding: #dma-cells = <1>; cell encodes the dw-edma channel id.
 */
static struct dma_chan *of_dw_edma_xlate(struct of_phandle_args *dma_spec,
					 struct of_dma *ofdma)
{
	struct of_dw_edma *edma = ofdma->of_dma_data;
	struct of_dw_edma_filter f;
	dma_cap_mask_t mask;

	if (dma_spec->args_count != 1) {
		dev_err(edma->dev, "invalid #dma-cells (got %u, want 1)\n",
			dma_spec->args_count);
		return NULL;
	}

	f.pci_dev = edma->pci_dev;
	f.chan_id = dma_spec->args[0];

	dma_cap_zero(mask);
	dma_cap_set(DMA_SLAVE, mask);
	dma_cap_set(DMA_PRIVATE, mask);

	return dma_request_channel(mask, of_dw_edma_chan_filter, &f);
}

static int of_dw_edma_probe(struct platform_device *pdev)
{
	struct of_dw_edma *edma;
	int ret;

	edma = devm_kzalloc(&pdev->dev, sizeof(*edma), GFP_KERNEL);
	if (!edma)
		return -ENOMEM;

	edma->dev = &pdev->dev;
	platform_set_drvdata(pdev, edma);

	edma->pci_dev = pci_get_device(EDMA_PCI_VENDOR, EDMA_PCI_DEVICE, NULL);
	if (!edma->pci_dev) {
		dev_err(&pdev->dev, "failed to find PCI device %04x:%04x\n",
			EDMA_PCI_VENDOR, EDMA_PCI_DEVICE);
		return -EPROBE_DEFER;
	}

	ret = of_dma_controller_register(pdev->dev.of_node,
					 of_dw_edma_xlate, edma);
	if (ret) {
		dev_err(&pdev->dev,
			"failed to register OF DMA controller: %d\n", ret);
		pci_dev_put(edma->pci_dev);
		return ret;
	}

	dev_info(&pdev->dev, "OF DMA controller registered\n");
	return 0;
}

static void of_dw_edma_remove(struct platform_device *pdev)
{
	struct of_dw_edma *edma = platform_get_drvdata(pdev);

	of_dma_controller_free(pdev->dev.of_node);
	pci_dev_put(edma->pci_dev);
}

static const struct of_device_id of_dw_edma_match[] = {
	{ .compatible = "amd,dw-edma" },
	{ }
};
MODULE_DEVICE_TABLE(of, of_dw_edma_match);

static struct platform_driver of_dw_edma_driver = {
	.probe = of_dw_edma_probe,
	.remove = of_dw_edma_remove,
	.driver = {
		.name = DRV_NAME,
		.of_match_table = of_dw_edma_match,
	},
};
module_platform_driver(of_dw_edma_driver);

MODULE_AUTHOR("AMD");
MODULE_DESCRIPTION("OF dw edma platform driver");
MODULE_LICENSE("GPL");
