# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: BW
# Maintainer: BW

SUMMARY = "MDB5 camera-to-PCIe video passthrough demo"
DESCRIPTION = "Streams cameras from a Versal Gen 2 endpoint over PCIe via the \
mdb5_v4l2 host driver and displays them on the x86 host, either as a tiled \
GStreamer window (mmap) or zero-copy to amdgpu DRM/KMS (dmabuf)."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COREBASE}/meta/files/common-licenses/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "\
    file://camera_simple.py \
    file://init.py \
    file://camera_mmap.py \
    file://camera_dmabuf.c \
    file://Makefile \
    file://install_mdb5_drv.sh \
    file://README.md \
"

S = "${WORKDIR}"

# camera_dmabuf uses only kernel uAPI headers (linux/videodev2.h, drm/*.h from
# linux-libc-headers) — no libdrm — so there is no build-time DEPENDS.

RDEPENDS:${PN} += "\
    python3-core \
    kmod \
    v4l-utils \
    tio \
    gstreamer1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
"

INSTALL_DIR = "${datadir}/pcie-camera-passthrough-simple"

do_compile() {
    oe_runmake camera_dmabuf CC="${CC}" CFLAGS="${CFLAGS}" LDFLAGS="${LDFLAGS}"
}

do_install() {
    install -d ${D}${INSTALL_DIR}
    install -m 0755 ${WORKDIR}/camera_simple.py    ${D}${INSTALL_DIR}/
    install -m 0755 ${WORKDIR}/camera_mmap.py      ${D}${INSTALL_DIR}/
    install -m 0644 ${WORKDIR}/init.py             ${D}${INSTALL_DIR}/
    install -m 0755 ${WORKDIR}/install_mdb5_drv.sh ${D}${INSTALL_DIR}/
    install -m 0755 ${WORKDIR}/camera_dmabuf       ${D}${INSTALL_DIR}/
    # Ship the C source + Makefile so the helper can be rebuilt on the host.
    install -m 0644 ${WORKDIR}/camera_dmabuf.c     ${D}${INSTALL_DIR}/
    install -m 0644 ${WORKDIR}/Makefile            ${D}${INSTALL_DIR}/
    install -m 0644 ${WORKDIR}/README.md           ${D}${INSTALL_DIR}/
}

FILES:${PN} += "${INSTALL_DIR}"
