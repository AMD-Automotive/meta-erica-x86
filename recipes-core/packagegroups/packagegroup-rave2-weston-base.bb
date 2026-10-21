# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

SUMMARY = "Common base for rave2 Weston images (graphics, media, dev tools)"
DESCRIPTION = "Shared package set common to rave2-buddy and rave2-weston-basic: \
the Weston/Mesa/DRM/Vulkan graphics stack, GStreamer + VA-API media stack, \
core build/debug tooling, and base runtime utilities. Product-specific \
components (menu UI, Xen, RAUC, Vivado/Xilinx, ADAU1860 audio, etc.) are \
added by the individual image recipes."
LICENSE = "MIT"

# Several deps (libdrm, libgbm, vulkan-loader, glibc-utils, ...) are dynamically
# renamed with ABI/version suffixes (e.g. libdrm -> libdrm2) under deb packaging.
# An allarch packagegroup (the packagegroup class default) can't reference those,
# so tie this packagegroup to the machine architecture instead. This MUST be set
# before "inherit packagegroup", because the class captures PACKAGE_ARCH at
# inherit time (PACKAGE_ARCH_EXPANDED := ...) to decide whether to inherit allarch.
PACKAGE_ARCH = "${MACHINE_ARCH}"

inherit packagegroup

RDEPENDS:${PN} = "\
    packagegroup-core-boot \
    kernel-modules \
    pciutils \
    linux-firmware \
    kmod \
    nano \
    packagegroup-core-buildessential \
    packagegroup-core-tools-debug \
    packagegroup-core-weston \
    mesa \
    mesa-demos \
    wayland-utils \
    mesa-megadriver \
    libgl-mesa \
    libegl-mesa \
    libgbm \
    libgles2-mesa \
    libdrm \
    libdrm-amdgpu \
    vulkan-loader \
    libva \
    libva-utils \
    gstreamer1.0-vaapi \
    gstreamer1.0 \
    gstreamer1.0-plugins-base-playback \
    gstreamer1.0-plugins-base-apps \
    gstreamer1.0-plugins-good-soup \
    gstreamer1.0-plugins-good-isomp4 \
    gstreamer1.0-plugins-good-audioparsers \
    gstreamer1.0-plugins-bad-faad \
    gstreamer1.0-libav \
    python3 \
    python3-pip \
    tar \
    rsync \
    glibc-utils \
    usbutils \
    minicom \
    devmem2 \
    htop \
    i2c-tools \
    evtest \
    strace \
    lsof \
    psmisc \
    resize-rootfs \
    e2fsprogs \
    udev-extraconf \
"
