# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

SUMMARY = "rave2-weston-basic: minimal x86 Weston development platform"
DESCRIPTION = "A subset of rave2-buddy that boots straight to a Weston-capable \
base platform for graphics/compositor development. No rave-menu, Xen, RAUC, \
Vivado/Xilinx tooling, or ADAU1860 audio hardware support."
LICENSE = "MIT"

IMAGE_INSTALL = "packagegroup-rave2-weston-base \
                virtio-msg-modules \
                ${CORE_IMAGE_EXTRA_INSTALL} \
"

# Step-1 qualification tools; keep this addition separate from the transport.
IMAGE_INSTALL:append:pn-rave2-weston-basic = " iperf3 fio"

GLIBC_GENERATE_LOCALES = " en_US.UTF-8 UTF-8"
IMAGE_LINGUAS = " en-us"

inherit core-image

# Keep SSH dev conveniences (empty password login) and passwordless sudo.
ROOTFS_POSTPROCESS_COMMAND:append = " ssh_sudo_policy_fixes;"
EXTRA_USERS_PARAMS += "usermod -a -G dialout amd-edf;"
EXTRA_USERS_SUDOERS = "amd-edf ALL=(ALL) NOPASSWD: ALL;"

ssh_sudo_policy_fixes() {
    # Enable remote SSH password auth for accounts that intentionally have
    # empty passwords in this dev image.
    if [ -e ${IMAGE_ROOTFS}${sysconfdir}/ssh/sshd_config ]; then
        sed -i -e 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' \
               -e 's/^#\\?PermitEmptyPasswords.*/PermitEmptyPasswords yes/' \
               ${IMAGE_ROOTFS}${sysconfdir}/ssh/sshd_config

        grep -q '^PasswordAuthentication' ${IMAGE_ROOTFS}${sysconfdir}/ssh/sshd_config || \
            echo 'PasswordAuthentication yes' >> ${IMAGE_ROOTFS}${sysconfdir}/ssh/sshd_config
        grep -q '^PermitEmptyPasswords' ${IMAGE_ROOTFS}${sysconfdir}/ssh/sshd_config || \
            echo 'PermitEmptyPasswords yes' >> ${IMAGE_ROOTFS}${sysconfdir}/ssh/sshd_config
    fi
}

### Bootloader overrides (non-Xen, non-RAUC direct boot)
GRUB_TIMEOUT = "5"
GRUB_TERMINAL = "console serial"
GRUB_SERIAL = "--unit=0 --speed=115200 --word=8 --parity=no --stop=1"
GRUB_OPTS = "serial --unit=0 --speed=115200 --word=8 --parity=no --stop=1;terminal_input console serial;terminal_output console serial"
APPEND += " quiet systemd.show_status=false loglevel=0 vt.global_cursor_default=0 console=ttyS0,115200n8 console=tty0"
SYSTEMD_DEFAULT_TARGET = "graphical.target"

INITRAMFS_IMAGE ?= "core-image-minimal-initramfs"

# Ensure the initramfs is built before our rootfs / boot image.
do_rootfs[depends] += "${INITRAMFS_IMAGE}:do_image_complete"
do_bootimg[depends] += "${INITRAMFS_IMAGE}:do_image_complete"

# Deploy a simple non-Xen grub.cfg plus the shared boot theme/initrd.
IMAGE_BOOT_FILES += "splash.png;EFI/BOOT/splash.png grub-weston-basic.cfg;EFI/BOOT/grub.cfg ${INITRAMFS_IMAGE}-${MACHINE}.cpio.gz;initrd EFI/BOOT/x86_64-efi/*.mod;EFI/BOOT/x86_64-efi unicode.pf2;EFI/BOOT/unicode.pf2 theme/theme.txt;EFI/BOOT/theme/theme.txt theme/background.png;EFI/BOOT/theme/background.png theme/select_c.png;EFI/BOOT/theme/select_c.png theme/select_w.png;EFI/BOOT/theme/select_w.png theme/select_e.png;EFI/BOOT/theme/select_e.png theme/DejaVuSans12.pf2;EFI/BOOT/theme/DejaVuSans12.pf2 theme/DejaVuSans14.pf2;EFI/BOOT/theme/DejaVuSans14.pf2 theme/DejaVuSans16.pf2;EFI/BOOT/theme/DejaVuSans16.pf2 theme/DejaVuSans18.pf2;EFI/BOOT/theme/DejaVuSans18.pf2 theme/DejaVuSansBold18.pf2;EFI/BOOT/theme/DejaVuSansBold18.pf2 theme/DejaVuSansBold20.pf2;EFI/BOOT/theme/DejaVuSansBold20.pf2"

IMAGE_ROOTFS_SIZE ?= "8192"
IMAGE_ROOTFS_EXTRA_SPACE:append = "${@bb.utils.contains("DISTRO_FEATURES", "systemd", " + 4096", "", d)}"
