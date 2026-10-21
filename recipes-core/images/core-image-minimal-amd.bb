# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

SUMMARY = "core-image-minimal with AMD microcode firmware and debug tools"

require recipes-core/images/core-image-minimal.bb

IMAGE_INSTALL += "linux-firmware"

# SSH server
IMAGE_INSTALL += "openssh-sshd openssh-ssh openssh-scp"

# Allow empty root password login over SSH
EXTRA_IMAGE_FEATURES += "allow-empty-password debug-tweaks"

# Debugging / diagnostic tools
IMAGE_INSTALL += " \
    iproute2 \
    ethtool \
    util-linux \
    procps \
    mcelog \
    e2fsprogs \
    kmod \
"

# Atlantic (Aquantia/Marvell AQC) PCIe ethernet driver
IMAGE_INSTALL += "kernel-module-atlantic"

# Audio support: ALSA userspace + HDA kernel modules
IMAGE_INSTALL += " \
    alsa-utils \
    alsa-state \
    alsa-ucm-conf \
    alsa-topology-conf \
    alsa-plugins \
"

# Strip all firmware except AMD microcode and atlantic to keep the image small
ROOTFS_POSTPROCESS_COMMAND += "strip_firmware_to_needed;"
strip_firmware_to_needed() {
    FWDIR="${IMAGE_ROOTFS}/usr/lib/firmware"
    SAVE="${WORKDIR}/fw-save"
    rm -rf "$SAVE"
    mkdir -p "$SAVE"
    # Preserve AMD microcode
    if [ -d "$FWDIR/amd-ucode" ]; then
        cp -a "$FWDIR/amd-ucode" "$SAVE/"
    fi
    # Preserve Atlantic (Aquantia) firmware
    if [ -d "$FWDIR/atlantic" ]; then
        cp -a "$FWDIR/atlantic" "$SAVE/"
    fi
    # Preserve amdgpu firmware (needed for HDMI/DP audio)
    if [ -d "$FWDIR/amdgpu" ]; then
        cp -a "$FWDIR/amdgpu" "$SAVE/"
    fi
    rm -rf "$FWDIR"/*
    cp -a "$SAVE"/* "$FWDIR/"
    rm -rf "$SAVE"
}

# Include all kernel modules (needed for HDA audio, USB, etc.)
IMAGE_INSTALL += "kernel-modules"

