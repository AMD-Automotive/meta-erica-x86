# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

SUMMARY = "Safe Display (x86) programmable-logic device tree overlay"
DESCRIPTION = "Compiles the x86 safe-display device tree overlay (x86.dtsi) into \
x86.dtbo. The overlay attaches the PL peripherals (Xilinx AXI GPIOs, rave-mq \
message queue, rave-kms CRTC) under the pl_bridge node exposed by the in-tree \
dw-edma device-tree interface, so the safe-display drivers can bind to them at \
runtime."
LICENSE = "GPL-2.0-or-later"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/GPL-2.0-or-later;md5=fed54355545ffd980b814dab4a3b312c"

SRC_URI = "file://x86.dtsi \
           file://safed-apply-overlay.sh \
           file://safed-dt-overlay.service \
           file://weston.service.d/10-safed-dt-overlay.conf \
"

S = "${WORKDIR}"

DEPENDS = "dtc-native"

# Runtime tools used by the boot-time apply script.
RDEPENDS:${PN} += "pciutils devmem2 kmod"

inherit systemd

SYSTEMD_SERVICE:${PN} = "safed-dt-overlay.service"

# Install location referenced by the boot-time apply service (stage 3.2).
DTBO_DIR = "${nonarch_base_libdir}/rave2-safed"

do_compile() {
    dtc -@ -I dts -O dtb -o ${B}/x86.dtbo ${S}/x86.dtsi
}

do_install() {
    install -d ${D}${DTBO_DIR}
    install -m 0644 ${B}/x86.dtbo ${D}${DTBO_DIR}/x86.dtbo

    install -d ${D}${sbindir}
    install -m 0755 ${S}/safed-apply-overlay.sh ${D}${sbindir}/safed-apply-overlay.sh

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${S}/safed-dt-overlay.service ${D}${systemd_system_unitdir}/safed-dt-overlay.service

    # Order the compositor after the overlay so the rave-kms DRM device
    # exists by the time Weston starts.
    install -d ${D}${sysconfdir}/systemd/system/weston.service.d
    install -m 0644 ${S}/weston.service.d/10-safed-dt-overlay.conf \
        ${D}${sysconfdir}/systemd/system/weston.service.d/10-safed-dt-overlay.conf
}

FILES:${PN} += "${DTBO_DIR}/x86.dtbo"

# Data-only package: no ELF, keep as machine-specific (it targets this kernel's PL).
PACKAGE_ARCH = "${MACHINE_ARCH}"
