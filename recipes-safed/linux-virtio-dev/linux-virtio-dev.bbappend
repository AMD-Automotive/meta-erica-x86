# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK, KC
# Maintainer: AK

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# Safe Display (x86) kernel integration.
#
# Stage 1.1: minimal device-tree entry point.
# x86 platforms usually boot without a firmware-provided device tree, which
# breaks OF overlay functionality. This patch synthesizes a minimal OF root and
# __symbols__ node when OF_OVERLAY is enabled and no root is present, providing
# the foundation for the runtime DT injection used by the safe-display stack.
#
# Stage 1.2: dw-edma (AMD MDB) support.
# Add AMD MDB PCIe endpoint support, non-LL transfer mode, and the OF proxy
# DMA / PCI-to-PL bridge device-tree interface to the DesignWare eDMA driver.
# These provide the PCIe DMA path used by the safe-display stack. The
# safed-edma.cfg fragment builds dw-edma as modules, which is what keeps this
# driver out of images other than rave2-safed-x86 -- see the fragment for why
# that matters. of-dw-edma.o is gated on CONFIG_OF_DYNAMIC (enabled in 1.1).
#
# Stage 2.6: Xilinx AXI GPIO controller.
# Enable CONFIG_GPIO_XILINX (gpio-xilinx) so the safe-display programmable-logic
# GPIO nodes (compatible "xlnx,xps-gpio-1.00.a") are driven. Built as a module.
# The safe-display kernel stack is applied unless a layer has turned it off.
# SAFED_KERNEL is defaulted in this layer's conf/layer.conf, where recipes other
# than this one can see it; meta-buddy sets it to 0. Anything other than an
# explicit "0" means on, so an unset variable cannot silently produce a kernel
# without these while rave2-safed-x86 still builds against it.
SAFED_KERNEL_SRC = " \
    file://safed-of.cfg \
    file://safed-edma.cfg \
    file://safed-gpio.cfg \
    file://0001-of-Enable-minimal-device-tree-entry-point.patch \
    file://0002-dmaengine-dw-edma-Add-AMD-MDB-Endpoint-Support.patch \
    file://0003-dmaengine-dw-edma-Add-non-LL-mode.patch \
    file://0004-dmaengine-dw-edma-Add-device-tree-interface.patch \
"

SRC_URI:append = "${@' ' + d.getVar('SAFED_KERNEL_SRC') if d.getVar('SAFED_KERNEL') != '0' else ''}"
