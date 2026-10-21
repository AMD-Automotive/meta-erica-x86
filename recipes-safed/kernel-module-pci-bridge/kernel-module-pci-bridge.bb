# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

SUMMARY = "PCI to PL bridge driver (x86 safe-display)"
DESCRIPTION = "Out-of-tree module for the x86 safe-display stack: pci-bridge binds \
to the amd,pci-pl-bridge PCIe endpoint and exposes the programmable logic bridge \
to the rest of the safe-display drivers."
LICENSE = "GPL-2.0-or-later"
LIC_FILES_CHKSUM = "file://pci_bridge.c;beginline=1;endline=1;md5=7226e442a172bcf25807246d7ef1eba1"

inherit module

KERNEL_MODULE_PACKAGE_SUFFIX = ""

SRC_URI = "file://src"

S = "${WORKDIR}/src"

EXTRA_OEMAKE += "LINUX_SRC=${STAGING_KERNEL_BUILDDIR}"

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra
    install -m 0644 ${S}/pci-bridge.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/pci-bridge.ko
}
