# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# Bump linux-firmware to 20260309 for fam1ah microcode (AMD Family 0x1a)
require recipes-kernel/linux-firmware/linux-firmware_20240909.bb

SRC_URI[sha256sum] = "c74cc6f562b58ad5bc6b2b00a61abc29c9e49e06126e7ba34fbca9928e07a96c"
WHENCE_CHKSUM = "772c8667ff568e7de3a7794fd420f2f7"

# License files that changed between 20240909 and 20260309
LIC_FILES_CHKSUM:remove = "\
    file://LICENSE.amd-ucode;md5=6ca90c57f7b248de1e25c7f68ffc4698 \
    file://LICENCE.cnm;md5=93b67e6bac7f8fec22b96b8ad0a1a9d0 \
"
LIC_FILES_CHKSUM:append = "\
    file://LICENSE.amd-ucode;md5=450f217aadc787514165e9568652d700 \
    file://LICENCE.cnm;md5=df3992006621b797e36de43f36336e36 \
"

# New Makefile removed install-nodedup; override PACKAGECONFIG to use plain install
PACKAGECONFIG[deduplicate] = "install,install,rdfind-native"
