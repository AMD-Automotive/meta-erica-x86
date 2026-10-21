# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

SUMMARY = "RAVE message queue kernel module (x86 safe-display)"
DESCRIPTION = "Out-of-tree module for the x86 safe-display stack: rave-mq binds to \
the amd,rave-message-queue device tree node and exposes a character device for \
message-queue communication with the programmable logic."
LICENSE = "GPL-2.0-or-later"
LIC_FILES_CHKSUM = "file://rave_mq.c;beginline=1;endline=1;md5=7226e442a172bcf25807246d7ef1eba1"

inherit module

KERNEL_MODULE_PACKAGE_SUFFIX = ""

SRC_URI = "file://src"

S = "${WORKDIR}/src"

EXTRA_OEMAKE += "LINUX_SRC=${STAGING_KERNEL_BUILDDIR}"

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra
    install -m 0644 ${S}/rave-mq.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/rave-mq.ko
}
