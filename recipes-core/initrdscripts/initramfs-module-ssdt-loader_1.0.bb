# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

SUMMARY = "Initramfs module for loading SSDT overlays via ACPI ConfigFS"
DESCRIPTION = "Loads SSDT overlays specified via kernel cmdline (ssdt=name1,name2) \
from /lib/firmware/acpi/ssdt-templates/ before switch_root, ensuring ACPI devices \
exist before any drivers probe."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/COPYING.MIT;md5=3da9cfbcb788c80a0384361b4de20420"

SRC_URI = "file://ssdt-loader"

RDEPENDS:${PN} = "initramfs-framework-base"

S = "${WORKDIR}"

do_install() {
    install -d ${D}/init.d
    install -m 0755 ${WORKDIR}/ssdt-loader ${D}/init.d/10-ssdt-loader
}

FILES:${PN} = "/init.d/10-ssdt-loader"
