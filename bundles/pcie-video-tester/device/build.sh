#!/bin/bash
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# build.sh — custom-build device script for "PCIe Video Tester".
#
# Shipped INSIDE the .rave2app bundle as `x86/build.sh`. It runs ON THE BOARD at
# install time (invoked by the App Store installer's custom-build path) and
# installs an Ubuntu-based x86 host straight into the application slot. The
# rootfs is built on-device here, so the bundle never redistributes Ubuntu.
#
# Contract with the installer (install_app_bundle.sh custom-build path):
#   * Runs as root, output line-buffered (streamed to the App Store terminal).
#   * Environment provided by the installer:
#       APP_SLOT_DEV          block device of the app slot (e.g. /dev/nvme0n1p4)
#       APP_SLOT_LABEL        FS label from the bundle `slot.label`     (default app-a)
#       APP_SLOT_MKFS_OPTIONS extra mkfs.ext4 options from `slot`       (default -O ^orphan_file)
#       BUNDLE                path to the bundle on disk (for overlay material, if any)
#   * This script FORMATS + MOUNTS the app slot, installs the rootfs (with its
#     own kernel under /boot), then UNMOUNTS it. It must NOT touch GRUB or the
#     grubenv — the installer's configure_boot() re-renders the ESP grub.cfg and
#     sets the boot flags after this returns.
#
# The app slot partition already exists (p4); we only format it — we never
# repartition the disk (unlike the standalone install_ubuntu.sh which carves
# p6/p7 out of free space).

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────
UBUNTU_RELEASE="${UBUNTU_RELEASE:-26.04}"
UBUNTU_CODENAME="${UBUNTU_CODENAME:-resolute}"
UBUNTU_ARCH="${UBUNTU_ARCH:-amd64}"
BASE_MIRROR="${BASE_MIRROR:-https://cdimage.ubuntu.com/ubuntu-base/releases}"
APT_MIRROR="${APT_MIRROR:-http://archive.ubuntu.com/ubuntu}"
SECURITY_MIRROR="${SECURITY_MIRROR:-http://security.ubuntu.com/ubuntu}"
DESKTOP_PACKAGE="${DESKTOP_PACKAGE:-ubuntu-desktop-minimal}"

# App slot the installer hands us (fall back to the conventional app-a/p4).
SLOT_DEV="${APP_SLOT_DEV:-/dev/nvme0n1p4}"
SLOT_LABEL="${APP_SLOT_LABEL:-app-a}"
SLOT_MKFS_OPTIONS="${APP_SLOT_MKFS_OPTIONS:--O ^orphan_file}"

NEW_USER="${NEW_USER:-amd}"
NEW_PASS="${NEW_PASS:-amd}"
NEW_HOSTNAME="${NEW_HOSTNAME:-pcie-video-tester}"

# Welcome GUI: shipped in the bundle as an uncompressed tar member and laid down
# into the slot at $APP_DIR. BUNDLE is the .rave2app path the installer hands us.
WELCOME_GUI_MEMBER="${WELCOME_GUI_MEMBER:-x86/welcome-gui.tar}"
APP_DIR="${WELCOME_GUI_APP_DIR:-/opt/welcome-gui}"

WORKDIR="${WORKDIR:-/var/lib/rave-menu/app-build}"
MNT="${APP_MNT:-/mnt/app-build}"

# ── Output helpers ───────────────────────────────────────────────────
step() { printf '\n==> %s\n' "$*"; }
log()  { printf '    %s\n' "$*"; }
die()  { printf '\n!!! ERROR: %s\n' "$*" >&2; exit 1; }

# ── Cleanup (always run) ─────────────────────────────────────────────
cleanup() {
    set +e
    for d in dev/pts dev sys proc run; do
        mountpoint -q "$MNT/$d" 2>/dev/null && umount -l "$MNT/$d" 2>/dev/null
    done
    mountpoint -q "$MNT" 2>/dev/null && umount -l "$MNT" 2>/dev/null
}
trap cleanup EXIT

# ── Preconditions ────────────────────────────────────────────────────
preflight() {
    step "Checking preconditions"
    [ "$(id -u)" = "0" ] || die "Must run as root."
    [ -b "$SLOT_DEV" ]   || die "App slot device not found: $SLOT_DEV"

    for t in mkfs.ext4 blkid wget tar chroot findmnt; do
        command -v "$t" >/dev/null 2>&1 || die "Required tool missing: $t"
    done
    log "App slot:  $SLOT_DEV (label $SLOT_LABEL)"
    log "Ubuntu:    $UBUNTU_RELEASE ($UBUNTU_CODENAME) / $DESKTOP_PACKAGE"
}

# ── Format + mount the app slot ──────────────────────────────────────
format_slot() {
    step "Formatting the application slot"
    # Make sure nothing is mounted from the slot before we reformat it.
    umount "$SLOT_DEV" 2>/dev/null || true
    mountpoint -q "$MNT" 2>/dev/null && umount -l "$MNT" 2>/dev/null || true

    # mkfs options come from the bundle's slot block (word-split intentionally).
    log "mkfs.ext4 ${SLOT_MKFS_OPTIONS} -L ${SLOT_LABEL} ${SLOT_DEV}"
    # shellcheck disable=SC2086
    mkfs.ext4 -F ${SLOT_MKFS_OPTIONS} -L "$SLOT_LABEL" "$SLOT_DEV"

    mkdir -p "$MNT" "$WORKDIR"
    mount "$SLOT_DEV" "$MNT"
}

# ── Download + extract the Ubuntu base rootfs ────────────────────────
bootstrap_rootfs() {
    step "Downloading the Ubuntu base system"
    local rel_url="${BASE_MIRROR}/${UBUNTU_RELEASE}/release"

    log "Resolving latest ${UBUNTU_RELEASE} base image ..."
    local sums fname expected
    sums=$(wget -q -O - "${rel_url}/SHA256SUMS") \
        || die "Could not fetch SHA256SUMS from ${rel_url}"
    fname=$(printf '%s\n' "$sums" \
        | grep -oE "ubuntu-base-${UBUNTU_RELEASE}[0-9.]*-base-${UBUNTU_ARCH}\.tar\.gz" \
        | sort -V | tail -n1)
    [ -n "$fname" ] || die "No ${UBUNTU_ARCH} base image listed for ${UBUNTU_RELEASE}."
    expected=$(printf '%s\n' "$sums" | grep " [*]\?${fname}\$" | awk '{print $1}' | head -n1)

    local tarball="$WORKDIR/$fname"
    local url="${rel_url}/${fname}"
    rm -f "$tarball"
    log "Source: $url"
    wget -q -O "$tarball" "$url" &
    local wpid=$!
    ( while kill -0 "$wpid" 2>/dev/null; do
          sz=$(stat -c %s "$tarball" 2>/dev/null || echo 0)
          printf '    downloaded %d MiB\n' "$(( sz / 1024 / 1024 ))"
          sleep 3
      done ) &
    local ppid=$!
    if ! wait "$wpid"; then
        kill "$ppid" 2>/dev/null || true
        die "Download failed: $url"
    fi
    kill "$ppid" 2>/dev/null || true
    log "Download complete ($(stat -c %s "$tarball" 2>/dev/null) bytes)."

    if [ -n "$expected" ]; then
        step "Verifying checksum"
        local actual
        actual=$(sha256sum "$tarball" | awk '{print $1}')
        [ "$actual" = "$expected" ] || die "Checksum mismatch for $fname."
        log "Checksum OK ($fname)."
    fi

    step "Extracting the base system"
    tar --numeric-owner -xpf "$tarball" -C "$MNT"
    rm -f "$tarball"
    log "Base system extracted to $SLOT_DEV."
}

# ── chroot helpers ───────────────────────────────────────────────────
mount_chroot() {
    step "Preparing the chroot environment"
    mount -t proc  proc     "$MNT/proc"
    mount -t sysfs sysfs    "$MNT/sys"
    mount --bind   /dev     "$MNT/dev"
    mount --bind   /dev/pts "$MNT/dev/pts"
    mount -t tmpfs tmpfs    "$MNT/run"
    cp -f /etc/resolv.conf "$MNT/etc/resolv.conf"
}

in_chroot() {
    LANG=C LC_ALL=C DEBIAN_FRONTEND=noninteractive \
        chroot "$MNT" /usr/bin/env \
            DEBIAN_FRONTEND=noninteractive \
            PATH=/usr/sbin:/usr/bin:/sbin:/bin \
            "$@"
}

# ── Configure apt + install desktop and kernel ───────────────────────
install_packages() {
    step "Configuring apt sources"
    cat > "$MNT/etc/apt/sources.list" <<EOF
deb ${APT_MIRROR} ${UBUNTU_CODENAME} main restricted universe multiverse
deb ${APT_MIRROR} ${UBUNTU_CODENAME}-updates main restricted universe multiverse
deb ${SECURITY_MIRROR} ${UBUNTU_CODENAME}-security main restricted universe multiverse
EOF

    cat > "$MNT/usr/sbin/policy-rc.d" <<'EOF'
#!/bin/sh
exit 101
EOF
    chmod +x "$MNT/usr/sbin/policy-rc.d"

    step "Updating package lists"
    in_chroot apt-get update

    step "Installing kernel and base utilities"
    # linux-generic pulls in linux-modules-<ver>-generic, which carries the
    # in-tree modules the mdb5_v4l2 driver links against (videodev,
    # videobuf2-common/-v4l2/-dma-sg, dw-edma, mc). build-essential/bc/kmod/
    # libelf-dev + linux-headers-generic are the out-of-tree build toolchain.
    # pciutils (lspci) and v4l-utils (v4l2-ctl) are the userspace tools the
    # PCIe video stack and the e2e test harness expect. The visual demo
    # (tests/pcie_video_tester.py) tiles the live streams with GStreamer and,
    # with --endpoint_host, drives the Versal over SSH, so it needs
    # gstreamer1.0-tools + the base/good plugins and an SSH client. htop is the
    # live load monitor shown in the console panes while the demo runs.
    # gstreamer1.0-x provides the X11 video sinks (xvimagesink/ximagesink):
    # under GNOME Wayland these higher-ranked X11 sinks make autovideosink
    # land on an XWayland window, which mutter decorates with a title bar and
    # resize borders. Without it autovideosink falls back to the borderless,
    # non-resizable glimagesink Wayland surface.
    in_chroot apt-get install -y --no-install-recommends \
        linux-generic linux-headers-generic initramfs-tools \
        build-essential bc kmod libelf-dev \
        pciutils v4l-utils \
        gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-x \
        openssh-client htop \
        sudo locales network-manager netplan.io \
        openssh-server \
        nano less ca-certificates

    step "Installing the Welcome GUI runtime"
    # PySide6 + pyserial + PyYAML + pyte drive the preflight GUI and the
    # split-screen Ryzen/Versal consoles; qt6-wayland + libxcb-cursor0 are the
    # Qt platform plugins the GNOME session needs. python3-ftdi (pyftdi, pulls
    # python3-usb) lets the PCIe check reboot the Versal over the FTDI cable when
    # the endpoint is missing. (NB: the target has no pip; everything comes from
    # apt — keep it that way.)
    in_chroot apt-get install -y --no-install-recommends \
        python3 \
        python3-pyside6.qtwidgets python3-pyside6.qtcore python3-pyside6.qtgui \
        python3-serial python3-yaml python3-pyte \
        python3-ftdi \
        qt6-wayland libxcb-cursor0

    step "Installing the Ubuntu desktop (this can take a while)"
    in_chroot apt-get install -y "$DESKTOP_PACKAGE"
}

# ── System configuration (user, autologin, fstab, locale) ────────────
configure_system() {
    step "Configuring the installed system"
    local root_uuid
    root_uuid=$(blkid -o value -s UUID "$SLOT_DEV")
    [ -n "$root_uuid" ] || die "Could not read app-slot filesystem UUID."

    log "Writing /etc/fstab"
    cat > "$MNT/etc/fstab" <<EOF
# Generated by the PCIe Video Tester custom-build installer
UUID=${root_uuid}  /      ext4  errors=remount-ro  0 1
EOF

    log "Setting hostname"
    echo "$NEW_HOSTNAME" > "$MNT/etc/hostname"
    cat > "$MNT/etc/hosts" <<EOF
127.0.0.1   localhost
127.0.1.1   ${NEW_HOSTNAME}
::1         localhost ip6-localhost ip6-loopback
EOF

    log "Generating locale (en_US.UTF-8)"
    echo "en_US.UTF-8 UTF-8" > "$MNT/etc/locale.gen"
    in_chroot locale-gen || true
    echo "LANG=en_US.UTF-8" > "$MNT/etc/default/locale"

    log "Creating user '${NEW_USER}' with sudo access"
    in_chroot useradd -m -s /bin/bash -G sudo "$NEW_USER" || true
    printf '%s:%s\n' "$NEW_USER" "$NEW_PASS" | in_chroot chpasswd
    echo "${NEW_USER} ALL=(ALL) NOPASSWD: ALL" > "$MNT/etc/sudoers.d/90-${NEW_USER}"
    chmod 0440 "$MNT/etc/sudoers.d/90-${NEW_USER}"

    log "Granting '${NEW_USER}' serial access (dialout) for the Versal console"
    # /dev/ttyUSB* are root:dialout 0660, so the GUI must run as a dialout member
    # to open the Versal serial console without sudo.
    in_chroot usermod -aG dialout "$NEW_USER" || true

    log "Enabling graphical autologin for '${NEW_USER}'"
    mkdir -p "$MNT/etc/gdm3"
    cat > "$MNT/etc/gdm3/custom.conf" <<EOF
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=${NEW_USER}
EOF

    mkdir -p "$MNT/etc/netplan"
    cat > "$MNT/etc/netplan/01-network-manager-all.yaml" <<EOF
network:
  version: 2
  renderer: NetworkManager
EOF
    chmod 0600 "$MNT/etc/netplan/01-network-manager-all.yaml"

    log "Enabling the SSH daemon on boot"
    # policy-rc.d blocks service start inside the chroot, so just enable the
    # unit; it will start on first boot. ssh.service ships a socket+service;
    # enabling ssh.service is the conventional way to bring it up at boot.
    in_chroot systemctl enable ssh.service || in_chroot systemctl enable ssh || true

    step "Building the initramfs"
    in_chroot update-initramfs -u -k all || in_chroot update-initramfs -c -k all

    rm -f "$MNT/usr/sbin/policy-rc.d"
}

# ── Welcome GUI autostart ────────────────────────────────────────────
# The app payload (/opt/welcome-gui) is deployed by the bundle's `welcome-gui`
# overlay component (post-deploy), so build.sh does NOT extract it here. All we
# need is a system-wide autostart entry so the GNOME session launches the GUI on
# graphical login. We write it to /etc/xdg/autostart (read for every user's
# session) which avoids per-user chown and works for the autologin user — and,
# crucially, needs nothing from $BUNDLE (which the custom-build path may not set).
install_welcome_autostart() {
    step "Enabling the Welcome GUI autostart"

    # State dir for the one-shot driver build marker (see drivers/). Owned by
    # the GUI user so the unprivileged app can write its install marker (the
    # build itself runs in a private temp dir; only `make install` uses sudo).
    mkdir -p "$MNT/var/lib/welcome-gui"
    in_chroot chown -R "${NEW_USER}:${NEW_USER}" /var/lib/welcome-gui || true

    # Prevent the mdb5_v4l2 driver from auto-loading at boot. It latches its
    # channel layout from the endpoint BRAM mailbox at probe time and never
    # re-syncs, so it must only be loaded on demand (by the e2e test harness,
    # after ep_proxy has stamped the mailbox) — never via udev modalias coldplug.
    # `blacklist` suppresses alias-based autoload only; an explicit
    # `modprobe mdb5_v4l2` still works. The driver check builds + installs the
    # module but does not load it; this rule keeps it dormant across reboots.
    mkdir -p "$MNT/etc/modprobe.d"
    cat > "$MNT/etc/modprobe.d/mdb5-v4l2.conf" <<'EOF'
# PCIe Video Tester: load mdb5_v4l2 on demand only (never auto-load at boot).
# The driver reads its channel layout from the endpoint mailbox at probe time,
# so it must be loaded after ep_proxy is up. Explicit `modprobe mdb5_v4l2` works.
blacklist mdb5_v4l2
EOF
    chmod 0644 "$MNT/etc/modprobe.d/mdb5-v4l2.conf"
    log "Installed modprobe blacklist for mdb5_v4l2 (on-demand load only)"

    local xdg_dir="$MNT/etc/xdg/autostart"
    mkdir -p "$xdg_dir"
    cat > "$xdg_dir/welcome-gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PCIe Video Tester — Welcome
Comment=Preflight checks and welcome screen for the PCIe Video Tester
Exec=${APP_DIR}/scripts/run-welcome-gui.sh
Icon=video-display
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
NoDisplay=false
Categories=Utility;
EOF
    chmod 0644 "$xdg_dir/welcome-gui.desktop"
    log "Autostart installed at /etc/xdg/autostart/welcome-gui.desktop"
    log "Exec -> ${APP_DIR}/scripts/run-welcome-gui.sh (deployed by the overlay)"

    # Desktop launcher icon. GNOME's desktop-icons (ding) requires the launcher
    # to be executable AND trusted; trusting needs a session D-Bus, so the
    # launcher self-trusts on first login (see run-welcome-gui.sh). We drop it
    # both on the autologin user's Desktop and in /etc/skel for future users.
    local desktop_entry
    desktop_entry="$(cat <<EOF
[Desktop Entry]
Type=Application
Name=PCIe Video Test
Comment=Preflight checks and welcome screen for the PCIe Video Tester
Exec=${APP_DIR}/scripts/run-welcome-gui.sh
Icon=video-display
Terminal=false
Categories=Utility;
EOF
)"
    local user_desktop="$MNT/home/${NEW_USER}/Desktop"
    local skel_desktop="$MNT/etc/skel/Desktop"
    mkdir -p "$user_desktop" "$skel_desktop"
    printf '%s\n' "$desktop_entry" > "$user_desktop/welcome-gui.desktop"
    printf '%s\n' "$desktop_entry" > "$skel_desktop/welcome-gui.desktop"
    chmod 0755 "$user_desktop/welcome-gui.desktop" "$skel_desktop/welcome-gui.desktop"
    in_chroot chown -R "${NEW_USER}:${NEW_USER}" "/home/${NEW_USER}/Desktop" || true
    log "Desktop launcher installed at ~${NEW_USER}/Desktop/welcome-gui.desktop"
}

# ── Main ─────────────────────────────────────────────────────────────
main() {
    preflight
    format_slot
    bootstrap_rootfs
    mount_chroot
    install_packages
    configure_system
    install_welcome_autostart
    # Leave the slot clean for the installer (it re-mounts for versal + boot).
    sync
    step "x86 host install complete"
    log "Ubuntu installed onto ${SLOT_DEV}; the installer will program Versal and set boot."
}

main "$@"
