#!/bin/sh
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: AK
# Maintainer: AK

# Apply the x86 safe-display PL device tree overlay.
#
# Mirrors the module-load + overlay-push sequence from the upstream
# safe-display boot_x86.sh, adapted for a Yocto image where the modules are
# installed into the rootfs and loaded via modprobe.
set -eu

OVERLAY="/usr/lib/rave2-safed/x86.dtbo"
CONFIGFS_PUSH="/sys/kernel/config/dt-overlays/push"
# MDB5 PCIe endpoint (dw-edma) identity. The BDF is not stable across boots, so
# the device is located by its PCI vendor:device ID (Xilinx 10ee:9038).
MDB5_PCI_ID="10ee:9038"
# The PL ready magic lives at the start (offset 0x0) of the endpoint's BAR3.
MDB5_MAGIC_BAR=3
PL_MAGIC="0xbadab00f"
RETRIES=30
SLEEP=2

log() { echo "safed-dt-overlay: $*"; }

# Resolve the physical base address of a given BAR of the first PCI device
# matching MDB5_PCI_ID. Echoes the base as 0x-prefixed hex, or nothing if the
# device / BAR is not present.
mdb5_bar_base() {
    _bar="$1"
    _want_vendor="0x${MDB5_PCI_ID%%:*}"
    _want_device="0x${MDB5_PCI_ID#*:}"
    for _dir in /sys/bus/pci/devices/*/; do
        [ "$(cat "$_dir/vendor" 2>/dev/null)" = "$_want_vendor" ] || continue
        [ "$(cat "$_dir/device" 2>/dev/null)" = "$_want_device" ] || continue
        # /sys/.../resource: one line per BAR: "start end flags" (hex).
        _start=$(awk -v b="$_bar" 'NR==b+1 {print $1}' "$_dir/resource" 2>/dev/null)
        [ -n "$_start" ] && [ "$_start" != "0x0000000000000000" ] || continue
        printf '0x%x\n' "$_start"
        return 0
    done
    return 1
}

# 1. Wait for the MDB5 PCIe endpoint to appear, rescanning the bus if needed.
#    Match by vendor:device ID rather than the PCI class string: the endpoint
#    may enumerate as an "Unassigned class [ff00]" device until a driver binds,
#    so a class-name grep is unreliable.
counter=0
while [ "$(lspci -n 2>/dev/null | grep -c "$MDB5_PCI_ID")" -eq 0 ]; do
    if [ "$counter" -ge "$RETRIES" ]; then
        log "MDB5 endpoint is missing, giving up."
        exit 1
    fi
    counter=$((counter + 1))
    log "MDB5 not initialized yet, rescanning PCI bus..."
    echo 1 > /sys/bus/pci/rescan || true
    sleep "$SLEEP"
done

# 2. Wait for the programmable logic behind MDB5 to report its ready magic.
#    The magic register sits at offset 0x0 of the endpoint's BAR3, whose base
#    address is discovered dynamically (the PCI BDF is not stable across boots).
counter=0
while :; do
    probe_addr=$(mdb5_bar_base "$MDB5_MAGIC_BAR" || true)
    val=""
    if [ -n "$probe_addr" ]; then
        val=$(devmem2 "$probe_addr" w 2>/dev/null | awk '/Read at address/ {print tolower($NF)}' || true)
    fi
    if [ "$val" = "$PL_MAGIC" ]; then
        log "PL is up (${probe_addr:-BAR$MDB5_MAGIC_BAR} = $val)."
        break
    fi
    if [ "$counter" -ge "$RETRIES" ]; then
        log "PL not ready (last read: ${val:-<none>}), giving up."
        exit 1
    fi
    counter=$((counter + 1))
    log "PL not ready yet (read: ${val:-<none>}), waiting..."
    sleep "$SLEEP"
done

# 3. Load the modules that must be present before the overlay is applied.
#    dtbo-config provides the configfs dt-overlays push interface; of-dw-edma
#    registers the pl_bridge OF node the overlay targets.
for mod in dtbo-config pci-bridge of-dw-edma rave-mq; do
    modprobe "$mod"
done

# 4. Push the overlay (idempotent: skip if rave-mq already instantiated).
if [ ! -e /dev/rave-mq ]; then
    log "Applying overlay $OVERLAY..."
    dd if="$OVERLAY" of="$CONFIGFS_PUSH"
else
    log "Overlay already applied (/dev/rave-mq present)."
fi

# 5. Fix up the rave-mq character device permissions.
if [ -e /dev/rave-mq ]; then
    chmod 660 /dev/rave-mq
    chown root:adm /dev/rave-mq
fi

# 6. Load the KMS driver last, once its dependencies are bound.
modprobe rave-kms

log "Overlay application complete."
