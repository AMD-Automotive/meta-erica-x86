# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: BW
# Maintainer: BW

SUMMARY = "MDB5 PCIe video bridge V4L2 host driver"
DESCRIPTION = "V4L2 capture driver for multi-channel video received from \
a Versal Gen 2 endpoint over PCIe using the Designware eDMA engine"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://mdb5_pci.c;beginline=1;endline=2;md5=66ce37e5d63d77a6658acaf1538da317"

SRC_URI = " \
    file://mdb5_pci.c \
    file://mdb5_channel.c \
    file://mdb5_dma.c \
    file://mdb5_bridge.h \
    file://mdb5_internal.h \
    file://Makefile \
"

S = "${WORKDIR}"

inherit module

do_compile() {
    unset CFLAGS CPPFLAGS CXXFLAGS LDFLAGS
    oe_runmake KDIR=${STAGING_KERNEL_BUILDDIR}
}

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/
    install -m 0644 mdb5_v4l2.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/
}

FILES:${PN} = "${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/mdb5_v4l2.ko"
