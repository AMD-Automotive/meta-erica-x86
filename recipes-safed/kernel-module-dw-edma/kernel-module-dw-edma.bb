# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

SUMMARY = "OF proxy dw-edma DMA controller and test client (x86 safe-display)"
DESCRIPTION = "Out-of-tree modules for the x86 safe-display PCIe DMA path: \
of-dw-edma registers an OF DMA controller (compatible amd,dw-edma) that proxies \
to the DesignWare eDMA PCIe channels, and dw-edma-test provides a loopback test \
client bound to amd,dw-edma-test device tree nodes."
LICENSE = "GPL-2.0-or-later"
LIC_FILES_CHKSUM = "file://of_dw_edma.c;beginline=1;endline=1;md5=7226e442a172bcf25807246d7ef1eba1"

inherit module

KERNEL_MODULE_PACKAGE_SUFFIX = ""

# This recipe ships two modules whose package names differ from PN. The module
# class creates them via a build-time dynamic split, so their names are unknown
# at parse time and image RDEPENDS on them cannot be mapped back to this recipe.
# Declare them in PACKAGES so the split targets are known during parsing and the
# recipe is pulled into images that depend on these packages.
PACKAGES:prepend = "kernel-module-of-dw-edma kernel-module-dw-edma-test "

SRC_URI = "file://src"

S = "${WORKDIR}/src"

EXTRA_OEMAKE += "LINUX_SRC=${STAGING_KERNEL_BUILDDIR}"

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra
    install -m 0644 ${S}/of-dw-edma.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/of-dw-edma.ko
    install -m 0644 ${S}/dw-edma-test.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/dw-edma-test.ko
}
