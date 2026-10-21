# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

SUMMARY = "Device tree overlay configfs management module (x86 safe-display)"
DESCRIPTION = "Out-of-tree module exposing a configfs interface \
(/sys/kernel/config/dt-overlays) to apply and remove device tree overlays at \
runtime. Used by the x86 safe-display stack to push the PL overlay onto the \
device tree injected by the dw-edma driver."
LICENSE = "GPL-2.0-or-later"
LIC_FILES_CHKSUM = "file://dtbo_config.c;beginline=1;endline=1;md5=7226e442a172bcf25807246d7ef1eba1"

inherit module

KERNEL_MODULE_PACKAGE_SUFFIX = ""

SRC_URI = "file://src"

S = "${WORKDIR}/src"

EXTRA_OEMAKE += "LINUX_SRC=${STAGING_KERNEL_BUILDDIR}"

do_install() {
    install -d ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra
    install -m 0644 ${S}/dtbo-config.ko \
        ${D}${nonarch_base_libdir}/modules/${KERNEL_VERSION}/extra/dtbo-config.ko
}
