# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK, KC
# Maintainer: AK

SUMMARY = "Safe Display (x86) kernel modules and runtime components"
DESCRIPTION = "Out-of-tree kernel modules that make up the x86 safe-display stack \
(dtbo-config, dw-edma OF proxy + test, pci-bridge, rave-mq, rave-kms) plus the \
in-tree Xilinx AXI GPIO driver they depend on and the PL device tree overlay."
LICENSE = "MIT"

# The safe-display module packages are machine-specific (built against the
# target kernel), so tie this packagegroup to the machine architecture rather
# than allarch. Must be set before "inherit packagegroup".
PACKAGE_ARCH = "${MACHINE_ARCH}"

inherit packagegroup

RDEPENDS:${PN} = "\
    kernel-module-dtbo-config \
    kernel-module-of-dw-edma \
    kernel-module-dw-edma-test \
    kernel-module-pci-bridge \
    kernel-module-rave-mq \
    kernel-module-rave-kms \
    kernel-module-gpio-xilinx \
    safed-dt-overlay \
    rave-mq \
    dashboard \
    weston-config-safed \
"

# Debugging / bring-up utilities.
#   mc            - Midnight Commander file manager
#   net-tools     - ifconfig (and friends)
#   libdrm-tests  - modetest (KMS/DRM mode testing)
#   kmod          - modprobe / insmod / rmmod / lsmod
#   dhcpcd        - DHCP client (ISC dhclient is no longer in poky)
RDEPENDS:${PN} += "\
    mc \
    net-tools \
    libdrm-tests \
    kmod \
    dhcpcd \
"
