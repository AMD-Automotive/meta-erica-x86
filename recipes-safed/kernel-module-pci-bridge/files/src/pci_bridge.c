// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * PCI to PL bridge driver
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */
#include <linux/mod_devicetable.h>
#include <linux/platform_device.h>
#include <linux/of_platform.h>

static int pci_bridge_probe(struct platform_device *pdev)
{
	int ret;

	ret = of_platform_populate(pdev->dev.of_node, NULL, NULL, &pdev->dev);
	if (ret < 0) {
		dev_err(&pdev->dev, "failed to populate devices: %d\n", ret);
		return ret;
	}

	return 0;
}

static void pci_bridge_remove(struct platform_device *pdev)
{
	of_platform_depopulate(&pdev->dev);
}

static const struct of_device_id pci_bridge_of_match[] = {
    { .compatible = "amd,pci-pl-bridge-0.1" },
    {}
};
MODULE_DEVICE_TABLE(of, pci_bridge_of_match);
 
static struct platform_driver pci_bridge_drv = {
    .probe = pci_bridge_probe,
    .remove = pci_bridge_remove,
    .driver = {
        .name = "pci-bridge",
        .of_match_table = pci_bridge_of_match,
    },
};
 
module_platform_driver(pci_bridge_drv);

MODULE_AUTHOR("Anatoliy Klymenko");
MODULE_DESCRIPTION("PCI PL Bridge Driver");
MODULE_LICENSE("GPL");