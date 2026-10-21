#!/bin/sh
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# Resize the root partition and filesystem to fill available disk space.
# Runs once at first boot, then disables itself.

set -e

ROOT_DEV=$(findmnt -n -o SOURCE /)
if [ -z "$ROOT_DEV" ]; then
    echo "resize-rootfs: cannot determine root device" >&2
    exit 1
fi

# Resolve to the underlying block device, e.g. /dev/sda2 -> /dev/sda + partition 2
DISK=$(lsblk -ndo PKNAME "$ROOT_DEV")
PARTNUM=$(cat "/sys/class/block/$(basename "$ROOT_DEV")/partition")

if [ -z "$DISK" ] || [ -z "$PARTNUM" ]; then
    echo "resize-rootfs: cannot determine parent disk or partition number" >&2
    exit 1
fi

echo "resize-rootfs: growing /dev/${DISK} partition ${PARTNUM} to fill disk"
# parted warns when resizing a mounted partition; answer Yes via pretend-input-tty
echo Yes | parted ---pretend-input-tty "/dev/${DISK}" resizepart "$PARTNUM" 100%

echo "resize-rootfs: resizing ext4 filesystem on ${ROOT_DEV}"
resize2fs "$ROOT_DEV"

echo "resize-rootfs: done, disabling service"
systemctl disable resize-rootfs.service
