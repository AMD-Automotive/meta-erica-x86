# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

SUMMARY = "RAVE Dashboard (x86 safe-display)"
DESCRIPTION = "Qt5/QML dashboard application for the x86 safe-display stack. \
It talks to the rave-mq character device (/dev/rave-mq) through the rave-mq \
userspace library to drive the display."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit cmake_qt5 systemd

SRC_URI = "file://src \
           file://dashboard.service"

S = "${WORKDIR}/src"

DEPENDS += "qtbase qtdeclarative rave-mq"
RDEPENDS:${PN} += "qtwayland weston"

SYSTEMD_SERVICE:${PN} = "dashboard.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

do_install() {
    install -d ${D}${bindir} ${D}${systemd_system_unitdir}
    install -m 0755 ${B}/dashboard ${D}${bindir}/dashboard
    install -m 0644 ${WORKDIR}/dashboard.service ${D}${systemd_system_unitdir}/dashboard.service
}
