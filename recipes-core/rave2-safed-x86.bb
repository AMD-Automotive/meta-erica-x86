# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC, AK
# Maintainer: KC

SUMMARY = "rave2-safed-x86: rave2-weston-basic plus the x86 safe-display stack"
DESCRIPTION = "The rave2-weston-basic development platform with the x86 \
safe-display stack added: the out-of-tree kernel modules (rave-kms, rave-mq, \
dw-edma, pci-bridge, dtbo-config), the PL device tree overlay, the dashboard \
application, and the matching Weston compositor configuration."

require recipes-core/rave2-weston-basic.bb

IMAGE_INSTALL += "packagegroup-rave2-safed"

# Without the kernel side this image builds and boots but the safe-display stack
# is quietly inert, which is a slow and confusing thing to discover on a bench.
# Only an explicit "0" disables, matching the kernel bbappend: the two gates have
# to agree, or an unset variable gives a kernel without the patches and an image
# that still builds against it.
# SkipRecipe rather than fatal: every recipe in the layer set is parsed whatever
# you asked to build, so failing here would break unrelated builds.
python () {
    if d.getVar("SAFED_KERNEL") == "0":
        raise bb.parse.SkipRecipe(
            "needs the safe-display kernel stack, but SAFED_KERNEL is 0. A "
            "layer in this build disables it -- meta-buddy does. Build this "
            "image from a layer set without it.")
}

# rave-kms / rave-mq DMA against the PL device needs the AMD IOMMU disabled,
# matching the safe-display reference setup.
APPEND += " amd_iommu=off"
