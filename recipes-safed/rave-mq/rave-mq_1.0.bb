# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

SUMMARY = "RAVE MQ userspace library and client (x86 safe-display)"
DESCRIPTION = "Userspace RAVE Message Queue library and a small client tool \
that talk to the rave-mq character device (/dev/rave-mq) exposed by the \
in-kernel rave-mq driver."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit cmake

SRC_URI = "file://src"

S = "${WORKDIR}/src"

do_install() {
    install -d ${D}${bindir} ${D}${libdir} ${D}${includedir}
    install -m 0755 ${B}/rave-mq-client ${D}${bindir}/rave-mq-client
    install -m 0644 ${B}/librave-mq.a ${D}${libdir}/librave-mq.a
    install -m 0644 ${S}/rave_mq.h ${D}${includedir}/rave_mq.h
}

FILES:${PN} += "${bindir}/rave-mq-client"
