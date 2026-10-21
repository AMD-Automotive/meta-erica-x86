#!/bin/sh
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# pack-app-bundle.sh — host-side packer that assembles a .rave2app bundle.
#
# Produces a gzipped tar whose FIRST member is appimage.yaml, followed by the
# component members, matching docs/app-bundle-installer/01-bundle-format.md.
# Versal images are stored gzipped; the x86 rootfs tar is stored uncompressed
# inside the (already gzipped) outer tar. Per-component sha256 sums are computed
# over the member as stored, and the whole-file sha256 is printed for the
# catalog entry.
#
# This is a thin convenience tool for testing/authoring; it supports the common
# kinds. For anything exotic, hand-author appimage.yaml and tar it yourself
# (appimage.yaml MUST be first).

set -eu

usage() {
    cat <<'EOF'
Usage: pack-app-bundle.sh --id ID --name NAME --version VER [options] --output DIR

Required:
  --id SLUG             App id (lowercase slug), e.g. xylon-surround-view
  --name "Text"         Human-facing name
  --version VER         semver-ish version, e.g. 2025.2.0
  --output DIR          Destination directory for the .rave2app

One application-image component is required (exactly one of):
  --rootfs-tar FILE     x86 rootfs tree tar (stored uncompressed) -> x86-rootfs-tar
  --rootfs-ext4 FILE    prebuilt ext4 image                       -> x86-rootfs-ext4
  --build-sh FILE       custom-build entry script (x86/build.sh)  -> custom-build
  --disk-part SPEC      one partition of a whole disk image       -> x86-disk-image
                        (repeatable, order matters; see below)

--disk-part SPEC is NAME:FILE:SIZE:TYPE:FS where
  NAME  partition name, must start with app-; one must be app-root
  FILE  raw partition image, stored uncompressed
  SIZE  size in MiB, or the word 'grow' for the one that fills the region
  TYPE  GPT type code (default 8300)
  FS    filesystem hint, e.g. vfat or ext4 (ext4 enables the grow step)
e.g.  --disk-part app-boot:p1.fat:80:0700:vfat \
      --disk-part app-root:p2.ext4:grow:8300:ext4 --chainload /xen.efi

Optional components (repeatable where it makes sense):
  --ospi FILE           Versal OSPI boot.bin (will be gzipped)     -> versal-ospi
  --wic FILE            Versal UFS wic.ufs   (will be gzipped)     -> versal-wic
  --overlay FILE[:TGT]  overlay tar, optional target (default /)   -> overlay
  --hook STAGE:FILE     hook script, STAGE = post-deploy|finalize  -> hook-script

Other:
  --vendor "Text"       Vendor string
  --description "Text"  Short description
  --boot-label "Text"   GRUB menu label (default: NAME)
  --make-default        Set boot.make_default: true (default false)
  --chainload PATH      boot.chainload for a disk-image app, e.g. /xen.efi
                        (not usable where the app's EFI loader cannot start)
  --entries-cfg PATH    boot.entries_cfg: the app's own GRUB entries file, on
                        its app-boot partition, adopted at install time
  --entry NAME          boot.entry: which menuentry to adopt (default: boot)
  --kernel PATH         boot.cmdline.kernel: the app's kernel, slot-relative
                        (e.g. /boot/vmlinuz). The alternative to --entries-cfg.
  --kernel-args ARGS    boot.cmdline.kernel_args. Do not put root= here; the
                        installer sets it, and declaring it is refused.
                        The device merges nothing into this line, so if your app
                        talks to anything hot-plugged you probably want:
                          pcie_ports=native pciehp.pciehp_force=1 hpbussize=32 \
                          hpmmiosize=512M hpmmioprefsize=1G
                        and a Xen guest also wants:
                          console=hvc0 clocksource=xen tsc=unstable earlyprintk=xen
                        See "What the device will not add for you" in the spec.
  --hypervisor PATH     boot.cmdline.hypervisor (e.g. /boot/xen.gz)
  --hypervisor-args ARGS  boot.cmdline.hypervisor_args
  --ramdisk PATH        boot.cmdline.ramdisk (e.g. /boot/initrd.img)
  --slot-label LABEL    slot.label (default app-a)
  -h, --help            Show this help.
EOF
}

die() { echo "pack-app-bundle: ERROR: $*" >&2; exit 1; }

sha256_of() { sha256sum "$1" | awk '{print $1}'; }

# --- Defaults ----------------------------------------------------------------
ID=""; NAME=""; VERSION=""; OUTPUT=""
ROOTFS_TAR=""; ROOTFS_EXT4=""; BUILD_SH=""
OSPI=""; WIC=""
VENDOR=""; DESCRIPTION=""; BOOT_LABEL=""; MAKE_DEFAULT="false"; SLOT_LABEL="app-a"
CHAINLOAD=""
ENTRIES_CFG=""
ENTRY=""
CMD_KERNEL=""
CMD_KERNEL_ARGS=""
CMD_HYP=""
CMD_HYP_ARGS=""
CMD_RAMDISK=""
DISK_PARTS=""  # newline-separated "name:file:size:type:fs"
OVERLAYS=""   # newline-separated "file:target"
HOOKS=""      # newline-separated "stage:file"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --id)           ID="$2"; shift 2 ;;
        --name)         NAME="$2"; shift 2 ;;
        --version)      VERSION="$2"; shift 2 ;;
        --output)       OUTPUT="$2"; shift 2 ;;
        --rootfs-tar)   ROOTFS_TAR="$2"; shift 2 ;;
        --rootfs-ext4)  ROOTFS_EXT4="$2"; shift 2 ;;
        --build-sh)     BUILD_SH="$2"; shift 2 ;;
        --disk-part)    DISK_PARTS="${DISK_PARTS}$2
"; shift 2 ;;
        --chainload)    CHAINLOAD="$2"; shift 2 ;;
        --entries-cfg)  ENTRIES_CFG="$2"; shift 2 ;;
        --entry)        ENTRY="$2"; shift 2 ;;
        --kernel)          CMD_KERNEL="$2"; shift 2 ;;
        --kernel-args)     CMD_KERNEL_ARGS="$2"; shift 2 ;;
        --hypervisor)      CMD_HYP="$2"; shift 2 ;;
        --hypervisor-args) CMD_HYP_ARGS="$2"; shift 2 ;;
        --ramdisk)         CMD_RAMDISK="$2"; shift 2 ;;
        --ospi)         OSPI="$2"; shift 2 ;;
        --wic)          WIC="$2"; shift 2 ;;
        --overlay)      OVERLAYS="${OVERLAYS}$2
"; shift 2 ;;
        --hook)         HOOKS="${HOOKS}$2
"; shift 2 ;;
        --vendor)       VENDOR="$2"; shift 2 ;;
        --description)  DESCRIPTION="$2"; shift 2 ;;
        --boot-label)   BOOT_LABEL="$2"; shift 2 ;;
        --make-default) MAKE_DEFAULT="true"; shift ;;
        --slot-label)   SLOT_LABEL="$2"; shift 2 ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "unknown argument: $1" ;;
    esac
done

[ -n "$ID" ]      || die "--id is required"
[ -n "$NAME" ]    || die "--name is required"
[ -n "$VERSION" ] || die "--version is required"
[ -n "$OUTPUT" ]  || die "--output is required"
[ -n "$BOOT_LABEL" ] || BOOT_LABEL="$NAME"

# Exactly one application-image component. The disk-image form contributes one
# component made of several members, so any number of --disk-part counts once.
appimg_count=0
[ -n "$ROOTFS_TAR" ]  && appimg_count=$((appimg_count + 1))
[ -n "$ROOTFS_EXT4" ] && appimg_count=$((appimg_count + 1))
[ -n "$BUILD_SH" ]    && appimg_count=$((appimg_count + 1))
[ -n "$DISK_PARTS" ]  && appimg_count=$((appimg_count + 1))
[ "$appimg_count" -eq 1 ] \
    || die "exactly one of --rootfs-tar/--rootfs-ext4/--build-sh/--disk-part is required"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/x86" "$WORK/x86/disk" "$WORK/versal" "$WORK/overlay" "$WORK/hooks"

# --- Stage component members + collect descriptor fragments ------------------
COMPONENTS=""

add_component() {
    # name kind path sha extra_yaml
    COMPONENTS="${COMPONENTS}  - name: $1
    kind: $2
    path: $3
    sha256: $4
$5"
}

if [ -n "$ROOTFS_TAR" ]; then
    [ -f "$ROOTFS_TAR" ] || die "rootfs tar not found: $ROOTFS_TAR"
    cp "$ROOTFS_TAR" "$WORK/x86/rootfs.tar"
    add_component "x86-rootfs" "x86-rootfs-tar" "x86/rootfs.tar" \
        "$(sha256_of "$WORK/x86/rootfs.tar")" "    target: app-slot
    required: false
"
elif [ -n "$ROOTFS_EXT4" ]; then
    [ -f "$ROOTFS_EXT4" ] || die "ext4 image not found: $ROOTFS_EXT4"
    cp "$ROOTFS_EXT4" "$WORK/x86/rootfs.ext4"
    add_component "x86-rootfs" "x86-rootfs-ext4" "x86/rootfs.ext4" \
        "$(sha256_of "$WORK/x86/rootfs.ext4")" "    target: app-slot
    required: false
"
elif [ -n "$BUILD_SH" ]; then
    [ -f "$BUILD_SH" ] || die "build script not found: $BUILD_SH"
    cp "$BUILD_SH" "$WORK/x86/build.sh"
    chmod +x "$WORK/x86/build.sh"
    add_component "custom-build" "custom-build" "x86/build.sh" \
        "$(sha256_of "$WORK/x86/build.sh")" "    required: false
"
elif [ -n "$DISK_PARTS" ]; then
    # One component, several members: the descriptor carries a `layout` list
    # instead of a single path/sha256 pair.
    LAYOUT=""
    have_root=0
    grow_count=0
    printf '%s' "$DISK_PARTS" | {
        while IFS= read -r spec; do
            [ -n "$spec" ] || continue
            pname="${spec%%:*}";  rest="${spec#*:}"
            pfile="${rest%%:*}";  rest="${rest#*:}"
            psize="${rest%%:*}";  rest="${rest#*:}"
            ptype="${rest%%:*}"
            pfs="${rest#*:}"
            [ "$ptype" = "$rest" ] && ptype="8300"
            [ -n "$pfs" ] || pfs=""
            case "$pname" in app-*) ;; *) die "--disk-part name must start with app-: $pname" ;; esac
            [ -f "$pfile" ] || die "partition image not found: $pfile"
            member="x86/disk/$(basename "$pfile")"
            cp "$pfile" "$WORK/$member"
            [ "$pname" = "app-root" ] && have_root=1
            LAYOUT="${LAYOUT}      - part: ${pname}
        path: ${member}
        sha256: $(sha256_of "$WORK/$member")
"
            if [ "$psize" = "grow" ]; then
                grow_count=$((grow_count + 1))
                LAYOUT="${LAYOUT}        grow: true
"
            else
                LAYOUT="${LAYOUT}        size_mib: ${psize}
"
            fi
            LAYOUT="${LAYOUT}        type: \"${ptype}\"
"
            [ -n "$pfs" ] && LAYOUT="${LAYOUT}        filesystem: ${pfs}
"
        done
        [ "$have_root" -eq 1 ] || die "one --disk-part must be named app-root"
        [ "$grow_count" -le 1 ] || die "at most one --disk-part may use 'grow'"
        printf '%s' "$LAYOUT" > "$WORK/.layout"
    }
    # The trailing newline matters: command substitution strips it, and without
    # it the next descriptor key would be appended to the last layout line.
    COMPONENTS="${COMPONENTS}  - name: app-disk
    kind: x86-disk-image
    layout:
$(cat "$WORK/.layout")
"
fi

if [ -n "$OSPI" ]; then
    [ -f "$OSPI" ] || die "OSPI image not found: $OSPI"
    gzip -c "$OSPI" > "$WORK/versal/boot.bin.gz"
    add_component "versal-ospi" "versal-ospi" "versal/boot.bin.gz" \
        "$(sha256_of "$WORK/versal/boot.bin.gz")" "    required: false
"
fi

if [ -n "$WIC" ]; then
    [ -f "$WIC" ] || die "WIC image not found: $WIC"
    gzip -c "$WIC" > "$WORK/versal/wic.ufs.gz"
    add_component "versal-wic" "versal-wic" "versal/wic.ufs.gz" \
        "$(sha256_of "$WORK/versal/wic.ufs.gz")" "    required: false
"
fi

ov_n=0
printf '%s' "$OVERLAYS" | while IFS= read -r spec; do
    [ -n "$spec" ] || continue
    ov_n=$((ov_n + 1))
    file="${spec%%:*}"
    tgt="/"
    case "$spec" in *:*) tgt="${spec#*:}" ;; esac
    [ -f "$file" ] || die "overlay tar not found: $file"
    cp "$file" "$WORK/overlay/overlay-${ov_n}.tar"
done
# (overlay/hook descriptor entries are emitted below from the staged files so
#  the sha matches the stored member exactly)

# Re-scan staged overlay/hook files to emit descriptor entries deterministically.
i=0
for f in "$WORK"/overlay/*.tar; do
    [ -e "$f" ] || continue
    i=$((i + 1))
    base="overlay/$(basename "$f")"
    add_component "app-overlay-${i}" "overlay" "$base" \
        "$(sha256_of "$f")" "    target: /
    stage: post-deploy
    required: false
"
done

# Hooks: STAGE:FILE
printf '%s' "$HOOKS" | while IFS= read -r spec; do
    [ -n "$spec" ] || continue
    stage="${spec%%:*}"
    file="${spec#*:}"
    case "$stage" in post-deploy|finalize) ;; *) die "hook stage must be post-deploy|finalize: $spec" ;; esac
    [ -f "$file" ] || die "hook script not found: $file"
    cp "$file" "$WORK/hooks/$(basename "$file")"
    chmod +x "$WORK/hooks/$(basename "$file")"
done
i=0
for f in "$WORK"/hooks/*; do
    [ -e "$f" ] || continue
    i=$((i + 1))
    # stage is recovered from HOOKS list by filename match
    bn="$(basename "$f")"
    stage="post-deploy"
    printf '%s' "$HOOKS" | while IFS= read -r spec; do
        [ -n "$spec" ] || continue
        [ "$(basename "${spec#*:}")" = "$bn" ] && echo "${spec%%:*}" > "$WORK/.stage.$bn"
    done
    [ -f "$WORK/.stage.$bn" ] && stage="$(cat "$WORK/.stage.$bn")"
    add_component "hook-${i}" "hook-script" "hooks/${bn}" \
        "$(sha256_of "$f")" "    stage: ${stage}
    required: false
"
done

# --- Render appimage.yaml ----------------------------------------------------
if [ -n "$ENTRIES_CFG" ] && [ -n "$CMD_KERNEL" ]; then
    echo "error: --entries-cfg and --kernel are alternatives; use one." >&2
    exit 2
fi
if [ -z "$ENTRIES_CFG" ] && [ -z "$CMD_KERNEL" ]; then
    echo "error: say how the app boots: --entries-cfg PATH (adopt an entry the" >&2
    echo "       app ships) or --kernel PATH (declare it). Without one the app" >&2
    echo "       would be booted with the menu's own arguments." >&2
    exit 2
fi
DESC="$WORK/appimage.yaml"
{
    echo "format: rave2-appimage"
    echo "format_version: 1"
    echo "id: ${ID}"
    echo "name: ${NAME}"
    echo "version: ${VERSION}"
    [ -n "$VENDOR" ] && echo "vendor: ${VENDOR}"
    [ -n "$DESCRIPTION" ] && echo "description: ${DESCRIPTION}"
    if [ -z "$DISK_PARTS" ]; then
        # A disk-image app brings its own partitions, so there is no slot to format.
        echo "slot:"
        echo "  filesystem: ext4"
        echo "  label: ${SLOT_LABEL}"
        echo "  mkfs_options: \"-O ^orphan_file\""
    fi
    echo "components:"
    printf '%s' "$COMPONENTS"
    echo "boot:"
    echo "  label: ${BOOT_LABEL}"
    echo "  make_default: ${MAKE_DEFAULT}"
    [ -n "$CHAINLOAD" ] && echo "  chainload: ${CHAINLOAD}"
    [ -n "$ENTRIES_CFG" ] && echo "  entries_cfg: ${ENTRIES_CFG}"
    [ -n "$ENTRIES_CFG" ] && echo "  entry: ${ENTRY:-boot}"
    if [ -n "$CMD_KERNEL" ]; then
        echo "  cmdline:"
        [ -n "$CMD_HYP" ]        && echo "    hypervisor: ${CMD_HYP}"
        [ -n "$CMD_HYP_ARGS" ]   && echo "    hypervisor_args: ${CMD_HYP_ARGS}"
        echo "    kernel: ${CMD_KERNEL}"
        [ -n "$CMD_KERNEL_ARGS" ] && echo "    kernel_args: ${CMD_KERNEL_ARGS}"
        [ -n "$CMD_RAMDISK" ]    && echo "    ramdisk: ${CMD_RAMDISK}"
    fi
} > "$DESC"

# --- Assemble the bundle (appimage.yaml FIRST) -------------------------------
mkdir -p "$OUTPUT"
OUT_FILE="${OUTPUT}/${ID}-${VERSION}.rave2app"

# Build the ordered member list: appimage.yaml first, then dirs that exist.
set -- appimage.yaml
[ -n "$(ls -A "$WORK/x86" 2>/dev/null)" ]     && set -- "$@" x86
[ -n "$(ls -A "$WORK/versal" 2>/dev/null)" ]  && set -- "$@" versal
[ -n "$(ls -A "$WORK/overlay" 2>/dev/null)" ] && set -- "$@" overlay
[ -n "$(ls -A "$WORK/hooks" 2>/dev/null)" ]   && set -- "$@" hooks

( cd "$WORK" && tar -cf - "$@" | gzip -1 ) > "$OUT_FILE"

WHOLE_SHA="$(sha256_of "$OUT_FILE")"

echo "Wrote ${OUT_FILE}"
echo "  whole-file sha256: ${WHOLE_SHA}"
echo "  (use this as the catalog entry's sha256)"
