# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://0001-udev-do-not-silence-initramfs-console-on-console-open-failure.patch;striplevel=0;patchdir=${WORKDIR} \
"
