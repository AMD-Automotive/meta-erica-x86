# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

FILESEXTRAPATHS:prepend := "${THISDIR}/${BPN}:"

SRC_URI += "file://rauc-slots.ignorelist"

do_install:append() {
    install -m 0644 ${WORKDIR}/rauc-slots.ignorelist \
        ${D}${sysconfdir}/udev/mount.ignorelist.d/rauc-slots.ignorelist
}
