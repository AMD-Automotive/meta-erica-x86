# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

SUMMARY = "Resize root partition and filesystem on first boot"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://resize-rootfs.sh \
           file://resize-rootfs.service \
          "

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "resize-rootfs.service"
SYSTEMD_AUTO_ENABLE = "enable"

RDEPENDS:${PN} = "parted e2fsprogs-resize2fs util-linux-findmnt util-linux-lsblk"

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ${WORKDIR}/resize-rootfs.sh ${D}${sbindir}/resize-rootfs.sh

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/resize-rootfs.service ${D}${systemd_system_unitdir}/resize-rootfs.service
}
