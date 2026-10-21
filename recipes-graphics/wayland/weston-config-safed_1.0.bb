# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

SUMMARY = "Weston compositor configuration for the x86 safe-display stack"
DESCRIPTION = "Ships the safe-display weston.ini and the weston.service drop-in \
that points the compositor at it and exposes the rave-kms card as a secondary \
output device. Kept out of the shared weston-init package so that products \
with a different compositor configuration (e.g. rave-menu in meta-buddy) can \
supply their own without colliding."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://safed.ini \
           file://weston.service.d/10-safed-weston.conf \
"

S = "${WORKDIR}"

RDEPENDS:${PN} = "weston-init"

do_install() {
    install -d ${D}${sysconfdir}/xdg/weston
    install -m 0644 ${WORKDIR}/safed.ini ${D}${sysconfdir}/xdg/weston/safed.ini

    install -d ${D}${sysconfdir}/systemd/system/weston.service.d
    install -m 0644 ${WORKDIR}/weston.service.d/10-safed-weston.conf \
        ${D}${sysconfdir}/systemd/system/weston.service.d/10-safed-weston.conf
}

FILES:${PN} = "${sysconfdir}/xdg/weston/safed.ini \
               ${sysconfdir}/systemd/system/weston.service.d/10-safed-weston.conf \
"
