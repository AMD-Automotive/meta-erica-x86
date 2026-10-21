#!/bin/sh
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# ubuntu-to-app-slot.sh — move an installed Ubuntu into the RAVE2 application slot.
#
# Run this ON THE BOARD, sitting in Buddy (the menu), so neither the source
# Ubuntu partition nor the app slot is the running system. It copies the Ubuntu
# install into the app slot, repairs the things that only worked because Ubuntu
# owned the whole disk, and registers it with the menu so it appears as the
# installed application.
#
#     sudo ./ubuntu-to-app-slot.sh --exclude /home/amd/yocto
#
# Afterwards the slot is an ordinary application, so bundles/app-clone captures
# and packages it in the usual way.
#
# The source partition is only ever mounted read-only; the app slot is
# reformatted, which is the destructive part and is confirmed before it happens.
set -eu

SRC_LABEL="ubuntu"
DST_LABEL="app-a"
APP_ID="ubuntu"
APP_NAME="Ubuntu"
APP_VERSION=""
SWAPFILE_MIB=0
RESET_IDENTITY=0
MAKE_DEFAULT=0
ASSUME_YES=0
DRY_RUN=0
VERIFY_ONLY=0
MKFS_OPTIONS="-O ^orphan_file"   # menu GRUB ext driver can't read orphan_file

SRC_MNT=""; DST_MNT=""; BOOT_MNT=""
EXTRA_EXCLUDES=""

log()  { printf '==> %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

find_esp() {
    for c in /boot/efi/EFI/BOOT /boot/efi/EFI/rave2 /boot/EFI/BOOT; do
        [ -f "${c}/grub.cfg" ] && { echo "$c"; return 0; }
    done
    return 1
}

# Check the slot against what bundles/app-clone needs to capture it and what the
# packer needs to accept the result. A slot that fails here still boots; it just
# cannot be turned into a bundle, and finding that out costs a multi-GiB capture
# and a rejection, so it is worth a few seconds now.
verify_cloneable() {
    _esp=$(find_esp) || die "no rave2 ESP found"
    _vm=$(mktemp -d /tmp/u2a-verify.XXXXXX)
    mount -o ro "$DST_DEV" "$_vm" || die "cannot mount ${DST_DEV} to verify it"
    _fail=0
    _ok()   { printf '    ok    %s\n' "$*"; }
    _bad()  { printf '    FAIL  %s\n' "$*"; _fail=1; }

    log "verifying the slot can be cloned"

    # capture-app.py: probe_installed_app() needs a record with an x86 kind, and
    # that kind must be one it treats as a rootfs.
    _rec=/var/lib/rave-menu/storage/installed-app.json
    _kind=$(sed -n 's/.*"kind"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$_rec" 2>/dev/null | head -n 1)
    case "$_kind" in
        x86-rootfs-tar|x86-rootfs-ext4|custom-build) _ok "installed-app.json kind=${_kind}" ;;
        "") _bad "installed-app.json missing or has no x86 kind (${_rec})" ;;
        *)  _bad "installed-app.json kind=${_kind} is not a rootfs kind" ;;
    esac

    # capture-app.py: verify_rootfs_tar() rejects a capture with no kernel here.
    if [ -f "${_vm}/boot/vmlinuz" ] || [ -f "${_vm}/boot/bzImage" ]; then
        _ok "kernel resolves under /boot"
    else
        _bad "no /boot/vmlinuz or /boot/bzImage that resolves to a real file"
    fi

    # capture-app.py: read_boot_contract() must find the contract, and
    # cmdline_from_deployed() must find a kernel in the entry, or the recipe
    # carries no boot and make_bundle.py's require_boot_declaration() refuses it.
    _mode=$(sed -n 's/^[[:space:]]*set[[:space:]]*app_mode=\(.*\)/\1/p' "${_esp}/app-boot.cfg" 2>/dev/null)
    [ -n "$_mode" ] && _ok "app-boot.cfg app_mode=${_mode}" \
                    || _bad "no app_mode in ${_esp}/app-boot.cfg"
    if grep -qE '^[[:space:]]*(linux|multiboot2)[[:space:]]+[^[:space:]]+' \
            "${_esp}/app-entry.cfg" 2>/dev/null; then
        _ok "app-entry.cfg names a kernel (becomes boot.cmdline in the recipe)"
    else
        _bad "${_esp}/app-entry.cfg has no linux/multiboot2 line to describe"
    fi

    # The clone is restored onto a slot whose UUIDs differ, so anything in fstab
    # naming a device by UUID will not be there. Root must be by LABEL, and the
    # swap partition must not be referenced at all.
    if grep -qE '^[[:space:]]*LABEL='"${DST_LABEL}"'[[:space:]]+/[[:space:]]' "${_vm}/etc/fstab" 2>/dev/null; then
        _ok "fstab root is LABEL=${DST_LABEL} (survives restore elsewhere)"
    else
        _bad "fstab root is not LABEL=${DST_LABEL}"
    fi
    if awk '$1 !~ /^#/ && $3 == "swap" {found=1} END {exit !found}' "${_vm}/etc/fstab" 2>/dev/null; then
        _bad "fstab still activates swap; a missing swap device costs a 90s boot timeout"
    else
        _ok "no active swap entry in fstab"
    fi
    _uuidrefs=$(awk '$1 ~ /^UUID=/ && $1 !~ /^#/ {print $1}' "${_vm}/etc/fstab" 2>/dev/null | head -n 3)
    [ -z "$_uuidrefs" ] && _ok "no UUID= device references left in fstab" \
                        || _bad "fstab still names devices by UUID: ${_uuidrefs}"

    # Not fatal, but a clone restored onto a second board would impersonate this
    # one, and that is discovered late and confusingly.
    if [ -s "${_vm}/etc/machine-id" ] || ls "${_vm}"/etc/ssh/ssh_host_* >/dev/null 2>&1; then
        printf '    note  carries this board machine-id/ssh host keys (--reset-identity clears them)\n'
    else
        _ok "identity cleared; a restored clone generates its own"
    fi

    umount "$_vm"; rmdir "$_vm"
    [ "$_fail" = "0" ] || die "this slot cannot be cloned as it stands (see FAIL above)"
    log "the slot is ready for bundles/app-clone/host/capture-app.py"
}

usage() {
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'USAGE'

Options:
  --source LABEL     partlabel of the Ubuntu partition   (default: ubuntu)
  --slot LABEL       partlabel of the app slot           (default: app-a)
  --exclude PATH     omit PATH from the copy (repeatable, rsync pattern,
                     anchored at the root of the Ubuntu install)
  --name NAME        application name shown by the menu  (default: Ubuntu)
  --id ID            application id                      (default: ubuntu)
  --swapfile MIB     create /swapfile of MIB inside the slot and use it
                     instead of the swap partition, which does not travel
  --reset-identity   clear machine-id and ssh host keys, so a restored clone
                     does not impersonate this board
  --make-default     boot the application by default (persists; see the note
                     at the end about testing with next_entry instead)
  --dry-run          report the plan and the size, change nothing
  --verify-only      re-check an already-transitioned slot, copy nothing
  -y, --yes          do not ask before reformatting the slot
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --source)   SRC_LABEL="${2:?}"; shift 2 ;;
        --slot)     DST_LABEL="${2:?}"; shift 2 ;;
        --exclude)  EXTRA_EXCLUDES="${EXTRA_EXCLUDES}${2:?}
"; shift 2 ;;
        --name)     APP_NAME="${2:?}"; shift 2 ;;
        --id)       APP_ID="${2:?}"; shift 2 ;;
        --swapfile) SWAPFILE_MIB="${2:?}"; shift 2 ;;
        --reset-identity) RESET_IDENTITY=1; shift ;;
        --make-default)   MAKE_DEFAULT=1; shift ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --verify-only) VERIFY_ONLY=1; shift ;;
        -y|--yes)   ASSUME_YES=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          die "unknown option: $1 (try --help)" ;;
    esac
done

[ "$(id -u)" = "0" ] || die "run me as root (sudo $0 ...)"

cleanup() {
    [ -n "$BOOT_MNT" ] && umount "$BOOT_MNT" 2>/dev/null || true
    [ -n "$DST_MNT" ]  && umount "$DST_MNT"  2>/dev/null || true
    [ -n "$SRC_MNT" ]  && umount "$SRC_MNT"  2>/dev/null || true
    for d in "$BOOT_MNT" "$DST_MNT" "$SRC_MNT"; do
        [ -n "$d" ] && rmdir "$d" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

# --- 1. Resolve and sanity-check both ends -----------------------------------

SRC_DEV="/dev/disk/by-partlabel/${SRC_LABEL}"
DST_DEV="/dev/disk/by-partlabel/${DST_LABEL}"
[ -e "$SRC_DEV" ] || die "no partition labelled '${SRC_LABEL}' on this board"
[ -e "$DST_DEV" ] || die "no partition labelled '${DST_LABEL}' on this board"
SRC_DEV=$(readlink -f "$SRC_DEV")
DST_DEV=$(readlink -f "$DST_DEV")
[ "$SRC_DEV" != "$DST_DEV" ] || die "source and slot are the same partition"

SRC_PARTUUID=$(blkid -s PARTUUID -o value "$SRC_DEV")
DST_PARTUUID=$(blkid -s PARTUUID -o value "$DST_DEV")

# Copying or reformatting the filesystem we are running from would give a torn
# snapshot, or destroy the running system. Both are worth refusing outright.
RUNNING=$(sed -n 's/.*root=PARTUUID=\([^ ]*\).*/\1/p' /proc/cmdline | tr 'A-Z' 'a-z')
if [ -n "$RUNNING" ]; then
    [ "$RUNNING" != "$(echo "$SRC_PARTUUID" | tr 'A-Z' 'a-z')" ] \
        || die "this board is booted FROM ${SRC_LABEL}. Reboot into Buddy and retry."
    [ "$RUNNING" != "$(echo "$DST_PARTUUID" | tr 'A-Z' 'a-z')" ] \
        || die "this board is booted FROM ${DST_LABEL}. Reboot into Buddy and retry."
fi

if ps w 2>/dev/null | grep -q '[i]nstall_app_bundle'; then
    die "an App Store install is running; wait for it to finish, then retry."
fi

log "source : ${SRC_LABEL} -> ${SRC_DEV} (PARTUUID ${SRC_PARTUUID})"
log "slot   : ${DST_LABEL} -> ${DST_DEV} (PARTUUID ${DST_PARTUUID})"

if [ "$VERIFY_ONLY" = "1" ]; then
    verify_cloneable
    exit 0
fi

# The menu automounts the app slot; it must be released before we reformat it.
for m in $(findmnt -rn -o TARGET --source "$DST_DEV" 2>/dev/null || true); do
    log "unmounting ${m} (the menu's view of the slot)"
    umount "$m" || die "cannot unmount ${m}; something is using the slot"
done

SRC_MNT=$(mktemp -d /tmp/u2a-src.XXXXXX)
mount -o ro "$SRC_DEV" "$SRC_MNT" || die "cannot mount ${SRC_DEV} read-only"
[ -d "${SRC_MNT}/etc" ] && [ -d "${SRC_MNT}/usr" ] \
    || die "${SRC_LABEL} does not look like a Linux root filesystem"

if [ -z "$APP_VERSION" ]; then
    APP_VERSION=$(sed -n 's/^VERSION_ID="\(.*\)"/\1/p' "${SRC_MNT}/etc/os-release" 2>/dev/null || true)
    [ -n "$APP_VERSION" ] || APP_VERSION="0.0.0"
fi
log "found  : $(sed -n 's/^PRETTY_NAME="\(.*\)"/\1/p' "${SRC_MNT}/etc/os-release" 2>/dev/null || echo "unknown OS")"

# --- 2. Work out what to copy, and whether it fits ---------------------------

# Pseudo-filesystems and caches only. Anything that is genuinely the user's --
# /home above all -- is left in unless they exclude it explicitly, so nothing
# disappears by surprise.
EXCL_FILE=$(mktemp /tmp/u2a-excl.XXXXXX)
cat > "$EXCL_FILE" <<'EOF'
/proc/*
/sys/*
/dev/*
/run/*
/tmp/*
/var/tmp/*
/media/*
/mnt/*
/lost+found
/var/cache/apt/archives/*.deb
/var/lib/apt/lists/*
/swapfile
/swap.img
EOF
[ -n "$EXTRA_EXCLUDES" ] && printf '%s' "$EXTRA_EXCLUDES" >> "$EXCL_FILE"
log "excluding:"
sed 's/^/      /' "$EXCL_FILE"

DST_BYTES=$(blockdev --getsize64 "$DST_DEV")
log "measuring the copy (this walks the tree; excluded directories are skipped)"
# Ask rsync itself, so the number reflects exactly the excludes the copy uses.
MEASURE_DIR=$(mktemp -d /tmp/u2a-measure.XXXXXX)
NEED_BYTES=$(rsync -aHAX --numeric-ids --dry-run --stats \
                 --exclude-from="$EXCL_FILE" "${SRC_MNT}/" "${MEASURE_DIR}/" 2>/dev/null \
             | sed -n 's/^Total file size: \([0-9,]*\) bytes.*/\1/p' | tr -d ',')
rmdir "$MEASURE_DIR" 2>/dev/null || true
[ -n "$NEED_BYTES" ] || die "could not measure the source with rsync"

log "to copy: $((NEED_BYTES / 1024 / 1024 / 1024)) GiB into a $((DST_BYTES / 1024 / 1024 / 1024)) GiB slot"
# ext4 metadata and the journal need room beyond the file data itself.
if [ "$NEED_BYTES" -gt $((DST_BYTES / 100 * 92)) ]; then
    die "this will not fit. Exclude the bulk (--exclude /home/<user>/<big-dir>),
       or enlarge the ${DST_LABEL} partition, and retry."
fi

if [ "$DRY_RUN" = "1" ]; then
    log "dry run: stopping before anything is written"
    exit 0
fi

if [ "$ASSUME_YES" != "1" ]; then
    printf '\n*** %s (%s) will be ERASED and replaced. Type YES to continue: ' \
        "$DST_LABEL" "$DST_DEV"
    read -r reply
    [ "$reply" = "YES" ] || die "aborted"
fi

# --- 3. Fresh slot, then copy ------------------------------------------------

log "formatting ${DST_DEV} as ext4 (label ${DST_LABEL}, opts ${MKFS_OPTIONS})"
# shellcheck disable=SC2086
mkfs.ext4 -F -L "$DST_LABEL" $MKFS_OPTIONS "$DST_DEV" >/dev/null 2>&1 \
    || die "mkfs.ext4 failed on ${DST_DEV}"
DST_PARTUUID=$(blkid -s PARTUUID -o value "$DST_DEV")

DST_MNT=$(mktemp -d /tmp/u2a-dst.XXXXXX)
mount "$DST_DEV" "$DST_MNT" || die "cannot mount the fresh slot"

log "copying (preserving hardlinks, ACLs and xattrs)"
# -X is not optional: the source carries file capabilities (setcap), and a copy
# that drops them leaves binaries that fail in ways nothing explains.
rsync -aHAX --numeric-ids --sparse --info=progress2 \
      --exclude-from="$EXCL_FILE" "${SRC_MNT}/" "${DST_MNT}/" \
    || die "copy failed"
rm -f "$EXCL_FILE"

# --- 4. Repair what assumed Ubuntu owned the disk ----------------------------

# 4a. fstab. The root entry named a filesystem UUID that no longer exists, and
# the swap partition lives outside the app slot, so it does not travel with a
# clone. A swap entry for an absent device costs a 90s systemd device timeout on
# every boot, so it goes rather than being left to fail.
FSTAB="${DST_MNT}/etc/fstab"
mkdir -p "${DST_MNT}/etc"
if [ -f "$FSTAB" ]; then
    awk '$1 ~ /^#/ {print; next}
         $2 == "/"      {next}
         $3 == "swap"   {print "# " $0 "   # dropped: swap partition is not part of the app slot"; next}
         $2 == "/boot/efi" {print "# " $0 "   # dropped: the ESP belongs to the menu"; next}
         {print}' "$FSTAB" > "${FSTAB}.new"
    mv "${FSTAB}.new" "$FSTAB"
fi
# LABEL=, not PARTUUID=: the label survives being restored onto another board,
# a PARTUUID does not.
printf 'LABEL=%s\t/\text4\tdefaults\t1 1\n' "$DST_LABEL" >> "$FSTAB"
log "fstab: root -> LABEL=${DST_LABEL}, swap and /boot/efi commented out"

# 4b. Hibernation. The initramfs may still hold a resume= for the swap partition
# we just dropped. Fixing the config is only half of it -- it needs a rebuilt
# initramfs to take effect -- so the boot entry also passes noresume.
if [ -d "${DST_MNT}/etc/initramfs-tools/conf.d" ]; then
    echo "RESUME=none" > "${DST_MNT}/etc/initramfs-tools/conf.d/resume"
    log "initramfs-tools: RESUME=none"
fi

# 4c. Optional swapfile, since the swap partition is gone.
if [ "$SWAPFILE_MIB" -gt 0 ]; then
    log "creating /swapfile (${SWAPFILE_MIB} MiB)"
    # Deliberately not sparse: a swapfile with holes cannot be swapped on.
    dd if=/dev/zero of="${DST_MNT}/swapfile" bs=1M count="$SWAPFILE_MIB" status=none
    chmod 600 "${DST_MNT}/swapfile"
    mkswap "${DST_MNT}/swapfile" >/dev/null
    printf '/swapfile\tnone\tswap\tsw\t0 0\n' >> "$FSTAB"
fi

# 4d. Identity. ssh host keys and machine-id travel with a byte-for-byte copy,
# so a clone restored onto a second board impersonates this one.
if [ "$RESET_IDENTITY" = "1" ]; then
    rm -f "${DST_MNT}"/etc/ssh/ssh_host_*
    : > "${DST_MNT}/etc/machine-id"
    rm -f "${DST_MNT}/var/lib/dbus/machine-id"
    log "identity cleared: machine-id and ssh host keys regenerate on first boot"
fi

# 4e. The kernel the menu will load. Ubuntu keeps /boot/vmlinuz as a relative
# symlink onto the current version, which GRUB follows and apt keeps current.
KERNEL=""; RAMDISK=""
for k in /boot/vmlinuz /boot/bzImage; do
    [ -e "${DST_MNT}${k}" ] && { KERNEL="$k"; break; }
done
[ -n "$KERNEL" ] || KERNEL=$(cd "$DST_MNT" && ls -1 boot/vmlinuz-* 2>/dev/null | sort -V | tail -n 1 | sed 's|^|/|')
[ -n "$KERNEL" ] || die "no kernel under /boot in the copy; this would not boot"
for r in /boot/initrd.img /boot/initrd; do
    [ -e "${DST_MNT}${r}" ] && { RAMDISK="$r"; break; }
done
[ -n "$RAMDISK" ] || RAMDISK=$(cd "$DST_MNT" && ls -1 boot/initrd.img-* 2>/dev/null | sort -V | tail -n 1 | sed 's|^|/|')
log "boot payload: ${KERNEL}${RAMDISK:+ + ${RAMDISK}}"

# 4f. Let the booted app say what it is, the way an installed bundle does.
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "${DST_MNT}/etc/rave2-app.json" <<JSON
{
  "id": "${APP_ID}",
  "name": "${APP_NAME}",
  "version": "${APP_VERSION}",
  "x86": { "kind": "custom-build", "slot": "a", "device": "${DST_DEV}", "label": "${DST_LABEL}", "sha256": "" },
  "versal": { "ospi_present": false, "ospi_sha256": "", "wic_present": false, "wic_sha256": "" },
  "installed_at": "${STAMP}"
}
JSON

sync
umount "$DST_MNT"; rmdir "$DST_MNT"; DST_MNT=""
umount "$SRC_MNT"; rmdir "$SRC_MNT"; SRC_MNT=""

# --- 5. Register it with the menu --------------------------------------------

ESP=""
ESP=$(find_esp) || die "no rave2 ESP found; cannot register the boot entry"

# Trailing digits of the device node, the same way install_app_bundle.sh does it.
IDX="${DST_DEV##*[!0-9]}"
[ -n "$IDX" ] || die "cannot work out the partition index of ${DST_DEV}"

{
    echo "# generated by ubuntu-to-app-slot.sh - do not edit"
    echo 'set root=($boot_disk,gpt'"${IDX}"')'
    echo "linux ${KERNEL} root=PARTUUID=${DST_PARTUUID} rw rootwait quiet noresume"
    [ -n "$RAMDISK" ] && echo "initrd ${RAMDISK}"
} > "${ESP}/app-entry.cfg"
{
    echo "# generated by ubuntu-to-app-slot.sh - do not edit"
    echo "set app_mode=entries"
    echo 'set app_part=$boot_disk,gpt'"${IDX}"
} > "${ESP}/app-boot.cfg"
log "wrote ${ESP}/app-boot.cfg and app-entry.cfg"

mkdir -p /var/lib/rave-menu/storage
cat > /var/lib/rave-menu/storage/installed-app.json <<JSON
{
  "id": "${APP_ID}",
  "name": "${APP_NAME}",
  "version": "${APP_VERSION}",
  "x86": { "kind": "custom-build", "slot": "a", "device": "${DST_DEV}", "label": "${DST_LABEL}", "sha256": "" },
  "versal": {
    "ospi_present": false, "ospi_sha256": "",
    "wic_present": false, "wic_sha256": ""
  },
  "installed_at": "${STAMP}"
}
JSON
log "wrote /var/lib/rave-menu/storage/installed-app.json"

if command -v grub-editenv >/dev/null 2>&1; then
    GRUBENV="${ESP}/grubenv"
    [ -f "$GRUBENV" ] || grub-editenv "$GRUBENV" create
    grub-editenv "$GRUBENV" set app_installed=1
    grub-editenv "$GRUBENV" set app_title="$APP_NAME"
    [ "$MAKE_DEFAULT" = "1" ] && grub-editenv "$GRUBENV" set boot_default=app
    log "registered with GRUB as '${APP_NAME}'"
else
    warn "grub-editenv missing; the menu may not offer the app entry"
fi

sync
verify_cloneable
cat <<EOF

==> Done. '${APP_NAME}' is now the installed application in slot ${DST_LABEL}.

    Test the boot ONCE, without committing to it:
        sudo grub-editenv ${ESP}/grubenv set next_entry=app-a && sudo reboot
    next_entry is cleared before the entry runs, so a hang costs one power cycle
    rather than looping forever. --make-default is the persistent version.

    Then bundle it:
        ./bundles/app-clone/host/capture-app.py <this-board-ip>

    Note: the copy carries this board's ssh host keys and machine-id unless you
    passed --reset-identity.
EOF
