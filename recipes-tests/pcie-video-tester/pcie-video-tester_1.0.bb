# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: BW
# Maintainer: BW

SUMMARY = "MDB5 PCIe video bridge visual streaming tester"
DESCRIPTION = "Loads the mdb5_v4l2 driver on the x86 host, discovers video \
devices, and displays live streams in tiled GStreamer windows. Optionally \
sets up vivid sources and ep_proxy on the endpoint via SSH."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/files/common-licenses/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "\
    file://pcie_video_tester.py \
"

S = "${WORKDIR}"

RDEPENDS:${PN} += "\
    python3-core \
    kmod \
    v4l-utils \
    gstreamer1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
"

INSTALL_DIR = "${datadir}/pcie-video-tester"

do_install() {
    install -d ${D}${INSTALL_DIR}
    install -m 0755 ${WORKDIR}/pcie_video_tester.py ${D}${INSTALL_DIR}/
}

FILES:${PN} += "${INSTALL_DIR}"
