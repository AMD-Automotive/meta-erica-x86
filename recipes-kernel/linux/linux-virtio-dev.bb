# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

COMPATIBLE_MACHINE:genericx86 = "genericx86"
COMPATIBLE_MACHINE:genericx86-64 = "genericx86-64"
KMACHINE:genericx86 ?= "common-pc"
KMACHINE:genericx86-64 ?= "common-pc-64"

inherit kernel
require recipes-kernel/linux/linux-yocto.inc

# provide this .inc to set specific revisions
include recipes-kernel/linux/linux-yocto-dev-revisions.inc

KBRANCH = "master"
KMETA = "kernel-meta"

SRC_URI = "git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git;branch=${KBRANCH};name=machine;protocol=https \
           git://git.yoctoproject.org/yocto-kernel-cache;type=kmeta;name=meta;branch=master;destsuffix=${KMETA};protocol=https \
           file://virtio.cfg \
           file://xen.cfg \
           file://usb.cfg \
           file://microcode.cfg \
           file://audio.cfg \
           file://spi.cfg \
           file://0001-ASoC-amd-acp-honor-DAIFMT-master-slave-for-I2S.patch \
           file://0002-ASoC-nau8822-keep-PLL-SDM-dither-active-on-integer-r.patch \
           file://0003-ASoC-nau8822-add-ACPI-match-for-NVTN2023.patch \
           file://0004-ASoC-amd-add-ACP70-machine-driver-entry-for-NAU8822-.patch \
           file://0005-ASoC-amd-add-I2S_BT-off-SoC-bridge-link-for-ACP70-NA.patch \
           file://0006-ASoC-amd-acp-make-tdm_mode-per-I2S-port.patch \
           "

SRC_URI:append:genericx86-64 = " file://x86.cfg "          

# v7.0-rc6 (2026-03-29)
SRCREV_machine ?= "7aaa8047eafd0bd628065b15757d9b48c5f9c07d"
SRCREV_meta ?= "d90bf62eec23b30d934c17799dd7de577bb886c7"

LINUX_VERSION ?= "7.0-rc6"
LINUX_VERSION_EXTENSION ?= "-mainline-${LINUX_KERNEL_TYPE}"
PV = "${LINUX_VERSION}+git"

LIC_FILES_CHKSUM = "file://COPYING;md5=6bc538ed5bd9a7fc9398086aedcd7e46"

# yaml and dtschema are required for 5.16+ device tree validation, libyaml is checked
# via pkgconfig, so must always be present, but we can wrap the others to make them
# conditional
DEPENDS += "libyaml-native"

PACKAGECONFIG ??= ""
PACKAGECONFIG[dt-validation] = ",,python3-dtschema-native"
# we need the wrappers if validation isn't in the packageconfig
DEPENDS += "${@bb.utils.contains('PACKAGECONFIG', 'dt-validation', '', 'python3-dtschema-wrapper-native', d)}"

COMPATIBLE_MACHINE = "^(qemuarmv5|qemuarm|qemuarm64|qemux86|qemuppc|qemumips|qemumips64|qemux86-64|qemuriscv32|qemuriscv64|qemuloongarch64)$"

KERNEL_DEVICETREE:qemuarmv5 = "arm/versatile-pb.dtb"

# Functionality flags
KERNEL_EXTRA_FEATURES ?= "features/netfilter/netfilter.scc features/taskstats/taskstats.scc"
KERNEL_FEATURES:append = " ${KERNEL_EXTRA_FEATURES}"
KERNEL_FEATURES:append:qemuall=" cfg/virtio.scc features/drm-bochs/drm-bochs.scc"
KERNEL_FEATURES:append:qemux86=" cfg/sound.scc cfg/paravirt_kvm.scc"
KERNEL_FEATURES:append:qemux86-64=" cfg/sound.scc cfg/paravirt_kvm.scc"
KERNEL_FEATURES:append = " ${@bb.utils.contains("TUNE_FEATURES", "mx32", " cfg/x32.scc", "", d)}"
KERNEL_FEATURES:append = " ${@bb.utils.contains("DISTRO_FEATURES", "ptest", " features/scsi/scsi-debug.scc", "", d)}"
KERNEL_FEATURES:append = " ${@bb.utils.contains("DISTRO_FEATURES", "ptest", " features/gpio/mockup.scc features/gpio/sim.scc", "", d)}"

KERNEL_VERSION_SANITY_SKIP = "1"

# Skip configcheck - kconfiglib in kernel-cache is too old for 7.0 Kconfig syntax
KCONF_AUDIT_LEVEL = "0"
KCONF_BSP_AUDIT_LEVEL = "0"
do_kernel_configcheck[noexec] = "1"
