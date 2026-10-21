#!/bin/sh -e
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

#
# init-install-efi.sh  –  rave2-buddy NVMe installer
#
# Called by setup-live as:
#   ./init.d/install-efi.sh <media_mount_name> <rootfs_image_name> ...
#
# On WIC media the rootfs is a raw ext4 partition (no rootfs.img file).
# This script discovers the source rootfs automatically.

PATH=/sbin:/bin:/usr/sbin:/usr/bin

# ── console ──────────────────────────────────────────────────────────
for c in /dev/console /dev/tty0; do
    if [ -c "$c" ]; then
        exec <"$c" >"$c" 2>&1
        break
    fi
done

# Allow Ctrl-C to abort (default is ignored when running as/under PID 1)
trap 'echo ""; echo "Aborted by user. Dropping to rescue shell ..."; exec sh 2>/dev/null || { while true; do sleep 3600; done; }' INT TERM

say() { echo "$*"; }

confirm() {
    say ""
    say "$*"
    printf "Type YES to continue, or anything else to abort: "
    read ans < /dev/tty0
    case "$ans" in
        YES|yes) return 0 ;;
        *) echo ""; echo "Aborted by user. Dropping to rescue shell ..."
           exec sh 2>/dev/null || { while true; do sleep 3600; done; } ;;
    esac
}

STEP=0
step() {
    STEP=$(( STEP + 1 ))
    say ""
    say "── Step $STEP: $* ──"
}

die() {
    echo ""
    echo "==========================================="
    echo "ERROR: $*"
    echo "==========================================="
    echo "Dropping to rescue shell ..."
    echo ""
    exec sh 2>/dev/null || while true; do sleep 3600; done
}

# ── arguments from setup-live ────────────────────────────────────────
MEDIA_NAME="$1"          # e.g. "sda1" or "sda1/" or "auto"
ROOT_IMAGE_NAME="$2"     # e.g. "rootfs.img" (may not exist on WIC media)

TARGET_DEVICE="/dev/nvme0n1"

say ""
say "============================================="
say " rave2-buddy NVMe Installer"
say "============================================="
say ""

# ── helper: is a device on the target disk? ──────────────────────────
on_target() {
    case "$1" in
        ${TARGET_DEVICE}*) return 0 ;;
        *) return 1 ;;
    esac
}

# ── helper: forcibly unmount all target partitions ────────────────────
unmount_target() {
    for p in ${TARGET_DEVICE}p*; do
        [ -b "$p" ] || continue
        umount "$p" 2>/dev/null || umount -l "$p" 2>/dev/null || true
    done
    awk -v tgt="${TARGET_DEVICE}" '$1 ~ tgt {print $2}' /proc/mounts | while read mp; do
        umount "$mp" 2>/dev/null || umount -l "$mp" 2>/dev/null || true
    done
}

# ── discover source rootfs ───────────────────────────────────────────
ROOTFS_IMG=""
ROOTFS_MNT=""

# Step 1: rootfs.img on the boot media?
if [ -n "$MEDIA_NAME" ] && [ "$MEDIA_NAME" != "auto" ]; then
    MEDIA_DIR="/run/media/${MEDIA_NAME%/}"
    if [ -f "$MEDIA_DIR/$ROOT_IMAGE_NAME" ]; then
        ROOTFS_IMG="$MEDIA_DIR/$ROOT_IMAGE_NAME"
        say "Found rootfs image: $ROOTFS_IMG"
    fi
fi

# Step 2: already-mounted partition labeled "root" (not on target)?
if [ -z "$ROOTFS_IMG" ]; then
    say "No rootfs.img file found — scanning for rootfs partition ..."
    for d in /run/media/*; do
        [ -d "$d" ] || continue
        dev=$(awk -v mp="$d" '$2 == mp {print $1; exit}' /proc/mounts)
        [ -n "$dev" ] || continue
        on_target "$dev" && continue
        lbl=$(blkid -o value -s LABEL "$dev" 2>/dev/null || true)
        if [ "$lbl" = "root" ]; then
            ROOTFS_MNT="$d"
            say "Found mounted rootfs partition: $dev at $d"
            break
        fi
    done
fi

# Step 3: unmounted partition labeled "root" on non-target device
if [ -z "$ROOTFS_IMG" ] && [ -z "$ROOTFS_MNT" ]; then
    say "No mounted rootfs partition — scanning block devices ..."
    for dev in /dev/sd[a-z][0-9]* /dev/sd[a-z][a-z][0-9]*; do
        [ -b "$dev" ] || continue
        on_target "$dev" && continue
        lbl=$(blkid -o value -s LABEL "$dev" 2>/dev/null || true)
        if [ "$lbl" = "root" ]; then
            say "Found unmounted rootfs partition: $dev (label=$lbl)"
            fstype=$(blkid -o value -s TYPE "$dev" 2>/dev/null || true)
            [ -z "$fstype" ] && fstype="ext4"
            mkdir -p /run/media/src-rootfs
            say "Attempting mount -t $fstype -o ro $dev /run/media/src-rootfs ..."
            if mount -t "$fstype" -o ro "$dev" /run/media/src-rootfs 2>&1; then
                ROOTFS_MNT="/run/media/src-rootfs"
                say "Mounted $dev at $ROOTFS_MNT"
                break
            else
                say "WARNING: mount with -t $fstype failed, trying without type ..."
                if mount -o ro "$dev" /run/media/src-rootfs 2>&1; then
                    ROOTFS_MNT="/run/media/src-rootfs"
                    say "Mounted $dev at $ROOTFS_MNT"
                    break
                fi
                say "WARNING: failed to mount $dev"
            fi
        fi
    done
fi

# Step 3b: udev may have automounted sda2 while we were scanning;
# re-check /run/media for any new mounts with label "root"
if [ -z "$ROOTFS_IMG" ] && [ -z "$ROOTFS_MNT" ]; then
    say "Re-scanning /run/media after block device probe ..."
    for d in /run/media/*; do
        [ -d "$d" ] || continue
        dev=$(awk -v mp="$d" '$2 == mp {print $1; exit}' /proc/mounts)
        [ -n "$dev" ] || continue
        on_target "$dev" && continue
        lbl=$(blkid -o value -s LABEL "$dev" 2>/dev/null || true)
        if [ "$lbl" = "root" ]; then
            ROOTFS_MNT="$d"
            say "Found rootfs partition (automounted by udev): $dev at $d"
            break
        fi
    done
fi

# Step 4: heuristic — any non-target mount with /usr/bin
if [ -z "$ROOTFS_IMG" ] && [ -z "$ROOTFS_MNT" ]; then
    for d in /run/media/*; do
        [ -d "$d/usr/bin" ] || continue
        dev=$(awk -v mp="$d" '$2 == mp {print $1; exit}' /proc/mounts)
        on_target "$dev" 2>/dev/null && continue
        ROOTFS_MNT="$d" && break
    done
fi

[ -z "$ROOTFS_IMG" ] && [ -z "$ROOTFS_MNT" ] && die "Cannot find source rootfs on install media.
Mounted media:
$(mount | grep /run/media || echo '  (none)')
Block devices:
$(cat /proc/partitions)
blkid:
$(blkid 2>/dev/null || echo '  (unavailable)')"

# ── validate target ──────────────────────────────────────────────────
[ -b "$TARGET_DEVICE" ] || die "Target NVMe disk not found: $TARGET_DEVICE"

# ── show plan and confirm ────────────────────────────────────────────
TGT_SIZE=$(cat /sys/block/${TARGET_DEVICE##*/}/size 2>/dev/null || echo "unknown")
if [ "$TGT_SIZE" != "unknown" ]; then
    TGT_GB=$(( TGT_SIZE / 2 / 1024 / 1024 ))
    say "Target disk: $TARGET_DEVICE (${TGT_GB} GB)"
else
    say "Target disk: $TARGET_DEVICE"
fi
if [ -n "$ROOTFS_IMG" ]; then
    say "Source:      rootfs image ($ROOTFS_IMG)"
else
    say "Source:      rootfs partition ($ROOTFS_MNT)"
fi
say ""
say "The following operations will be performed:"
say "  1. Wipe and repartition $TARGET_DEVICE (ALL DATA WILL BE LOST)"
say "  2. Format partitions (EFI + menu-a + menu-b + app-a + rave-data)"
say "  3. Copy rootfs to menu slot A"
say "  4. Install RAUC-aware boot config and reboot"
say " *** THIS WILL DESTROY ALL DATA ON $TARGET_DEVICE ***"
confirm "Ready to install to $TARGET_DEVICE?"

# ── partition target ─────────────────────────────────────────────────
BOOTFS=${TARGET_DEVICE}p1
MENU_A=${TARGET_DEVICE}p2
MENU_B=${TARGET_DEVICE}p3
APP_A=${TARGET_DEVICE}p4
APP_B=${TARGET_DEVICE}p5

step "Partitioning $TARGET_DEVICE"
[ ! -e /etc/mtab ] && [ -e /proc/mounts ] && ln -sf /proc/mounts /etc/mtab

say "Unmounting any existing target partitions ..."
unmount_target
sleep 1

say "Wiping partition table on $TARGET_DEVICE ..."
dd if=/dev/zero of=$TARGET_DEVICE bs=512 count=35 conv=notrunc 2>/dev/null

say "Creating GPT partition layout ..."
parted -s $TARGET_DEVICE mklabel gpt
parted -s $TARGET_DEVICE mkpart EFI       fat32  1MiB     513MiB
parted -s $TARGET_DEVICE set 1 esp on
parted -s $TARGET_DEVICE mkpart menu-a    ext4   513MiB   16897MiB
parted -s $TARGET_DEVICE mkpart menu-b    ext4   16897MiB 33281MiB
parted -s $TARGET_DEVICE mkpart app-a     ext4   33281MiB 84481MiB
parted -s $TARGET_DEVICE mkpart rave-data ext4   84481MiB 135681MiB
parted -s $TARGET_DEVICE print
partprobe $TARGET_DEVICE 2>/dev/null || true

# udev may re-mount NVMe partitions after partprobe
unmount_target
sleep 1

say "Waiting for partition device nodes ..."
C=0
while [ $C -lt 15 ]; do
    [ -b "$BOOTFS" ] && [ -b "$MENU_A" ] && [ -b "$MENU_B" ] && [ -b "$APP_A" ] && [ -b "$APP_B" ] && break
    C=$(( C + 1 ))
    sleep 1
done
[ -b "$BOOTFS" ] && [ -b "$MENU_A" ] && [ -b "$MENU_B" ] && [ -b "$APP_A" ] && [ -b "$APP_B" ] \
    || die "Partition device nodes did not appear: $BOOTFS $MENU_A $MENU_B $APP_A $APP_B"

step "Formatting partitions"
unmount_target
mkfs.vfat -F 32 -n EFI          "$BOOTFS"
mkfs.ext4 -F -L menu-a          "$MENU_A"
mkfs.ext4 -F -L menu-b          "$MENU_B"
mkfs.ext4 -F -L app-a           "$APP_A"
mkfs.ext4 -F -m 1 -L rave-data  "$APP_B"

step "Initializing bulk storage"
mkdir -p /tgt_data
mount "$APP_B" /tgt_data || die "Could not mount bulk storage $APP_B"
mkdir -p /tgt_data/uploads /tgt_data/payload-cache /tgt_data/app-cache /tgt_data/updates \
    || die "Could not create bulk storage directories"
sync
echo "version=1" > /tgt_data/.rave-storage-v1.tmp
sync
mv /tgt_data/.rave-storage-v1.tmp /tgt_data/.rave-storage-v1
sync
umount /tgt_data || die "Could not unmount bulk storage $APP_B"

# ── copy rootfs to menu slot A ───────────────────────────────────────
step "Copying rootfs to menu slot A"
mkdir -p /tgt_root
mount "$MENU_A" /tgt_root

if [ -n "$ROOTFS_IMG" ]; then
    mkdir -p /src_root
    mount -o ro,loop "$ROOTFS_IMG" /src_root
    say "Copying rootfs from image ($ROOTFS_IMG) ..."
    cp -a /src_root/* /tgt_root/
    umount /src_root
elif [ -n "$ROOTFS_MNT" ]; then
    say "Copying rootfs from partition ($ROOTFS_MNT) ..."
    cp -a "$ROOTFS_MNT"/* /tgt_root/
fi

step "Configuring fstab"
if [ -d /tgt_root/etc ]; then
    boot_uuid=$(blkid -o value -s UUID "$BOOTFS")
    root_uuid=$(blkid -o value -s UUID "$MENU_A")
    say "Regenerating fstab for NVMe slot A (root=$root_uuid, boot=$boot_uuid) ..."
    # Keep only virtual-fs and comment lines from the original fstab;
    # drop all /dev/* and UUID=* lines (they reference the USB media).
    grep -vE '^[[:space:]]*/dev/|^[[:space:]]*UUID=' /tgt_root/etc/fstab \
        > /tmp/fstab.new 2>/dev/null || true
    # Add NVMe root and EFI entries
    [ -n "$root_uuid" ] && echo "UUID=$root_uuid  /          ext4  defaults  1 1" >> /tmp/fstab.new
    [ -n "$boot_uuid" ] && echo "UUID=$boot_uuid  /boot/efi  vfat  defaults  1 2" >> /tmp/fstab.new
    mv /tmp/fstab.new /tgt_root/etc/fstab
    say "Installed fstab:"
    cat /tgt_root/etc/fstab
fi
sync
umount /tgt_root
say "Menu system rootfs installed to slot A ($MENU_A)."

# ── copy boot artifacts ─────────────────────────────────────────────
step "Installing boot artifacts"
mkdir -p /tgt_boot
mount "$BOOTFS" /tgt_boot
EFIDIR="/tgt_boot/EFI/BOOT"
mkdir -p "$EFIDIR"

BOOT_MEDIA=""
for d in /run/media/*; do
    [ -d "$d/EFI/BOOT" ] && BOOT_MEDIA="$d" && break
done
[ -z "$BOOT_MEDIA" ] && die "Cannot find EFI/BOOT on install media."

# Restore USB grub.cfg default back to "RAVE2 Buddy" (the rave-menu
# sets it to "Install to NVME" to trigger the installer; reset it so the
# USB boots normally if re-used without re-flashing).
if [ -f "$BOOT_MEDIA/EFI/BOOT/grub.cfg" ]; then
    sed -i "s/^set default='Install to NVME'$/set default='RAVE2 Buddy'/" "$BOOT_MEDIA/EFI/BOOT/grub.cfg"
    say "Restored USB grub.cfg default to RAVE2 Buddy."
fi

cp -a "$BOOT_MEDIA"/EFI/BOOT/* "$EFIDIR/" 2>/dev/null || true
for payload in bzImage xen.gz initrd; do
    [ -e "$BOOT_MEDIA/$payload" ] && cp -f "$BOOT_MEDIA/$payload" /tgt_boot/
done

# ── ADAU1860 SSDT ────────────────────────────────────────────────────
# The SSDT is NOT baked into the early-ACPI override (kernel/firmware/acpi/
# in the initrd) here.  Doing so loaded the PMOD1860 codec on every boot
# regardless of the menu toggle, which (a) defeated the enable/disable
# switch and (b) wedged the whole acp70-nau8822 card in EPROBE_DEFER when
# the dev-kit codec was absent or unresponsive, killing NAU8822 + HDMI too.
#
# The codec is now gated solely by the toggle: rave-menu sets grubenv
# `ssdt_enabled=adau1860`, GRUB injects `ssdt=adau1860` on the kernel
# cmdline, and the initramfs ssdt-loader module loads the template from
# /lib/firmware/acpi/ssdt-templates/ via ACPI ConfigFS before switch_root.

# ── Install RAUC-aware GRUB config ───────────────────────────────────
step "Installing RAUC-aware GRUB config"

# Get identifiers for all rootfs slots
menu_a_partuuid=$(blkid -o value -s PARTUUID "$MENU_A" 2>/dev/null || true)
menu_b_partuuid=$(blkid -o value -s PARTUUID "$MENU_B" 2>/dev/null || true)
app_a_partuuid=$(blkid -o value -s PARTUUID "$APP_A" 2>/dev/null || true)

# Use the RAUC-aware template if available, otherwise patch the USB config
if [ -f "$BOOT_MEDIA/grub-nvme-rauc.cfg" ]; then
    say "Using RAUC-aware GRUB config template ..."
    GRUBCFG="$EFIDIR/grub.cfg"
    cp "$BOOT_MEDIA/grub-nvme-rauc.cfg" "$GRUBCFG"

    # Substitute placeholder PARTUUID values
    [ -n "$menu_a_partuuid" ] && sed -i "s|@@MENU_A_PARTUUID@@|$menu_a_partuuid|g" "$GRUBCFG"
    [ -n "$menu_b_partuuid" ] && sed -i "s|@@MENU_B_PARTUUID@@|$menu_b_partuuid|g" "$GRUBCFG"
    [ -n "$app_a_partuuid" ]  && sed -i "s|@@APP_A_PARTUUID@@|$app_a_partuuid|g"   "$GRUBCFG"
else
    say "WARNING: grub-nvme-rauc.cfg not found on install media — falling back to USB config"
    if [ -f "$BOOT_MEDIA/EFI/BOOT/grub.cfg" ]; then
        GRUBCFG="$EFIDIR/grub.cfg"
        cp "$BOOT_MEDIA/EFI/BOOT/grub.cfg" "$GRUBCFG"
        sed -i "/menuentry 'install'/,/^}/d" "$GRUBCFG"
        sed -i "/^initrd /d" "$GRUBCFG"
        sed -i "s/ LABEL=[^ ]*//" "$GRUBCFG"
        if [ -n "$menu_a_partuuid" ]; then
            sed -i "s| root=[^ ]*| root=PARTUUID=$menu_a_partuuid|g" "$GRUBCFG"
        fi
    fi
fi

say "Installed grub.cfg:"
cat "$EFIDIR/grub.cfg"

# ── Create initial RAUC grubenv ──────────────────────────────────────
step "Creating RAUC grubenv"

# grubenv must be exactly 1024 bytes: "# GRUB Environment Block\n" header
# followed by key=value pairs, padded with '#' characters.
GRUBENV_FILE="$EFIDIR/grubenv"

# Build the content
GRUBENV_CONTENT="# GRUB Environment Block
ORDER=A B
A_TRY=0
A_OK=1
B_TRY=0
B_OK=0
"

# Write and pad to exactly 1024 bytes
printf '%s' "$GRUBENV_CONTENT" > "$GRUBENV_FILE"
GRUBENV_SIZE=$(wc -c < "$GRUBENV_FILE")
PAD_SIZE=$(( 1024 - GRUBENV_SIZE ))
if [ $PAD_SIZE -gt 0 ]; then
    dd if=/dev/zero bs=1 count=$PAD_SIZE 2>/dev/null | tr '\0' '#' >> "$GRUBENV_FILE"
fi

say "Created grubenv (slot A active, marked good)"

sync

# Install a copy of our GRUB under a named EFI path that Ubuntu
# (and any other OS installer) will leave untouched.  The fallback
# /EFI/BOOT/ copy is kept for firmware that ignores boot entries.
RAVE_EFIDIR="/tgt_boot/EFI/rave2"
mkdir -p "$RAVE_EFIDIR"
cp -a "$EFIDIR"/* "$RAVE_EFIDIR/" 2>/dev/null || true

sync
umount /tgt_boot
say "Boot partition installed to $BOOTFS (RAUC A/B aware)."

if command -v efibootmgr >/dev/null 2>&1; then
    step "Registering EFI boot entry"

    # Determine the ESP disk and partition number for efibootmgr.
    # BOOTFS is e.g. /dev/nvme0n1p1 -> disk=/dev/nvme0n1, part=1
    efi_disk="${BOOTFS%p[0-9]*}"
    efi_part="${BOOTFS##*p}"

    # Remove any previous "RAVE2 Buddy" (and legacy "RAVE2 Board Menu") entries to avoid duplicates
    efibootmgr 2>/dev/null | grep -E "RAVE2 Buddy|RAVE2 Board Menu" | \
        sed 's/^Boot\([0-9A-Fa-f]*\).*/\1/' | while read num; do
            efibootmgr -b "$num" -B 2>/dev/null || true
        done

    # Create a named boot entry pointing to our GRUB
    efibootmgr -c -d "$efi_disk" -p "$efi_part" \
        -L "RAVE2 Buddy" \
        -l "\\EFI\\rave2\\bootx64.efi" 2>/dev/null || true

    say "Registered EFI boot entry: RAVE2 Buddy"
    efibootmgr -v 2>/dev/null || true
fi

say ""
say "============================================="
say " Installation complete!"
say "============================================="
say ""
say "Please remove the USB drive before rebooting."
say ""
printf "Press ENTER to reboot ... "
read dummy < /dev/tty0
say "Rebooting ..."
sync
reboot -f 2>/dev/null || reboot
