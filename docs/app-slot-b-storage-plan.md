<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# Repurpose Application Slot B as Bulk Storage

## Goal

Repurpose the currently unused second application partition,
`/dev/nvme0n1p5`, as persistent bulk storage for RAVE2 Buddy without changing
the two-slot RAUC menu update model or the single-slot application install
model.

The storage must support:

- Versal Lab browser uploads and temporary decompression/programming files.
- App Store bundle download, inspection, and installer work files.
- RAUC update bundle staging.
- Migration through a RAUC update on an already-installed system.
- Initialization during a new NVMe installation.

Every future RAUC bundle must support a direct upgrade from a legacy image that
still treats `p5` as the unused `app-b` partition. There is no intermediate
release prerequisite and no point at which the legacy migration path may be
removed.

The compatibility baseline is the customer image published at
`https://rave2.cicdstorage.com/welcome/rave2-buddy.wic.gz`. Direct upgrade tests
must start from that image and use its installed RAUC executable and userspace.

## Current State

The repository and the target at `192.168.0.146` show this layout:

| Partition | Size | Current purpose | RAUC-owned |
| --- | ---: | --- | --- |
| `p1` | 512 MiB | EFI system partition | No |
| `p2` | 16 GiB | Menu rootfs A | Yes, `rootfs.0` |
| `p3` | 16 GiB | Menu rootfs B | Yes, `rootfs.1` |
| `p4` | 50 GiB | Application A | No |
| `p5` | 50 GiB | Reserved application B, unused | No |

RAUC 1.15.2 reports that the target is booted from `rootfs.0` and manages only
`p2` and `p3`. Repurposing `p5` therefore does not reduce RAUC's atomic update
or rollback capability.

`app_slots.py` only permits application installs to `p4`; `p5` is explicitly
reserved and no current application install path writes it. On the inspected
target, `p5` is an empty ext4 filesystem labelled `app-b`, containing only
`lost+found`.

The active rootfs has 8.6 GiB free. Its current high-volume paths are:

| Data class | Current path | Lifecycle | Target observation |
| --- | --- | --- | ---: |
| Lab uploads | `/var/lib/rave-menu/uploads` | User-managed, persistent | Empty |
| Lab expansion/download cache | `/var/lib/rave-menu/payload-cache` | Temporary; removed by programming scripts | Absent |
| App Store cache and work dirs | `/var/lib/rave-menu/app-cache` | Temporary, except explicitly retained bundles | Empty |
| RAUC bundle staging | `/var/lib/rave-menu/updates` | Temporary | Empty |
| Vivado installer state/uploads | `/var/lib/rave-menu/vivado` | Mixed persistent and temporary | 12 KiB |
| Built-in board-test payloads | `/usr/lib/firmware/boardtest` | Packaged defaults | 1.9 GiB |
| Installed app recovery payloads | `p4:/lib/versal` | Persistent, owned by installed app | No app installed |

The two firmware locations are not interchangeable:

- `/usr/lib/firmware/boardtest/{wic.ufs,boot.bin}` contains menu-owned defaults
  used when Versal Lab is run without explicit files. These files remain
  packaged in every RAUC menu rootfs; they are not candidates for `p5` storage.
- `p4:/lib/versal/{wic.ufs.gz,boot.bin.gz,installed.json}` belongs to the
  installed application and supports application recovery. It should remain on
  `p4`, because replacing the application should atomically replace its recovery
  payloads as well.

## Recommended Layout

Keep the partition number and size unchanged, but change the meaning of `p5`:

- GPT partition name: `rave-data`
- ext4 filesystem label: `rave-data`
- primary mount point: `/var/lib/rave-menu/storage`

The GPT name is informational. Fresh installations may use `rave-data`, but OTA
migration must not rewrite the GPT merely to change `app-b`; the filesystem
label and marker establish ownership.

Use directories on that filesystem and bind mount them onto existing paths:

| Storage directory | Existing path presented to applications |
| --- | --- |
| `storage/uploads` | `/var/lib/rave-menu/uploads` |
| `storage/payload-cache` | `/var/lib/rave-menu/payload-cache` |
| `storage/app-cache` | `/var/lib/rave-menu/app-cache` |
| `storage/updates` | `/var/lib/rave-menu/updates` |

Do not mount `p5` over all of `/var/lib/rave-menu`. Small state such as
`app-stores.json`, Vivado install state, and future database/configuration files
should remain slot-local unless they are deliberately classified and migrated.
This avoids silently changing persistence and rollback semantics for unrelated
state.

Keep `/var/lib/rave-menu/vivado` out of the first change. Its installed tools
live under `/opt/tools`, its state is explicitly copied by the RAUC post-install
hook, and its upload cleanup has different semantics. It can be moved later if
measurements show a real peak-space problem.

Use a dedicated systemd oneshot service rather than relying only on `/etc/fstab`.
The service should run before `rave-menu.service`, mount `p5`, create the
managed directories with explicit ownership/modes, and establish the bind
mounts. This gives one place for validation, logging, and fail-safe behavior.
`rave-menu.service` should run after the setup service but must still start if
the bulk filesystem cannot be mounted. In that degraded state, the bind mounts
are not established and the existing directories on the menu rootfs remain
visible and writable. The UI must report that bulk storage is unavailable and
that capacity is limited to the active rootfs.

Use the fixed NVMe partition path initially because the board layout and all
existing slot code already use `/dev/nvme0n1pN`. Verify both filesystem label
and partition number before mounting. A label alone is unsafe when removable
media can contain a duplicate label.

Add `p4` and `p5` to the udev automount ignore list. `p2` and `p3` are already
ignored. Managed storage must not race an automount under `/run/media`, and
application installation already mounts `p4` explicitly.

## Data Lifecycle and Capacity Rules

### Versal Lab

Browser uploads remain visible at `/var/lib/rave-menu/uploads`, but the bytes
reside on `p5`. Programming scripts continue to use
`/var/lib/rave-menu/payload-cache`, also on `p5`, for URL downloads and
decompressed `.gz` files. Existing cleanup traps remain responsible for
temporary cache removal.

Update the free-space checks to account for peak use, not only compressed input
size. For a gzipped payload the required space is approximately:

`existing uploads + compressed input + decompressed output + safety reserve`

Uploads should continue to use a `.part` file followed by atomic rename. Add a
preflight based on `Content-Length` when available. Space exhaustion is handled
as an operation failure with normal temporary-file cleanup; the design does not
promise that every combination of retained and expanded payloads will fit.

Expected individual programming and application payloads are up to 20 GiB. Do
not block implementation on proving a worst-case capacity formula for every
combination of compressed input, expanded payload, and retained files.

### App Store

The normal streamed `x86-rootfs-tar` path already writes the application image
directly to `p4`, so it does not need a second full copy on `p5`. Its descriptor,
dispatch files, and any streamed overlay/hook temporary files remain under the
bind-mounted `app-cache` work directory.

Legacy `x86-rootfs-ext4` bundles and explicitly retained bundles are downloaded
to `app-cache` on `p5`. Space checks must include the full cached bundle, any
extracted member that exists concurrently, work files, and the common reserve.
Cleanup must continue on success and failure.

App Store bundles are currently transient. Planned support for uploading an
App Store bundle before installation must store that uploaded bundle on `p5`
and account for the uploaded archive plus installation workspace. The upload
workflow itself is a follow-up task, but this storage layout must not require a
later partition or mount migration to add it.

The application-owned recovery firmware remains under `p4:/lib/versal` and is
not copied to `p5`.

### RAUC Updates

RAUC bundles downloaded through the menu are staged under the bind-mounted
`updates` directory on `p5`. RAUC still writes only the inactive menu slot.
The bundle cleanup trap removes staging files after success or failure.

This avoids consuming active-rootfs space during an update, but it does not
make `p5` a RAUC slot. Do not add it to `system.conf` or to
`RAUC_BUNDLE_SLOTS`.

### Built-in Board-Test Firmware

Keep `/usr/lib/firmware/boardtest/{wic.ufs,boot.bin}` packaged in every RAUC
rootfs and retain the existing default resolution in
`program_remote_versal_payload.sh`. The payload is intentionally duplicated
across menu slots so every installed or rollback rootfs is self-contained.

Do not copy, bind mount, or seed board-test firmware onto `p5`. The data
partition is for mutable uploads and temporary installation workspace, not for
files owned by the menu image.

## Migration Through RAUC

The migration must work in one update from any legacy image because a customer
may skip any number of releases. A RAUC update writes a new rootfs but does not
preserve arbitrary files from the old rootfs, so the bundle hook must prepare
`p5` and migrate persistent uploads while the old rootfs is still active.

All future bundles must retain this legacy detection and migration logic. It is
not a temporary compatibility mechanism.

Extend the bundle hook with two phases:

Declare both hook phases in `rave2-buddy-bundle.bb`: the bundle-level
`install-check` and the rootfs slot's existing `post-install`. The current hook
is documented to continue after all errors and always exit zero; split that
policy so optional Vivado state propagation remains best-effort and storage
checks produce explicit diagnostics. Storage failures must also remain
non-fatal to the RAUC update; they select degraded mode instead.

### Permanent Install Preflight

Run a bundle `install-check` before RAUC writes the inactive rootfs:

1. Verify that `/dev/nvme0n1p5` exists and is not referenced by RAUC.
2. If it has a valid `.rave-storage-vN` marker, validate the mounted filesystem
  in place when already mounted at the expected storage location. This is the
  normal path for later network updates whose RAUC bundle is being read from
  `storage/updates`; do not unmount that filesystem during installation.
3. If the filesystem label is `app-b` and no recognized marker exists, classify
  it as legacy application slot B. `p5` has never been used as a supported
  application slot, so migration may erase it regardless of its contents. Log
  that destructive initialization will occur.
4. Treat `rave-data` with a missing or corrupt marker, an unexpected label or
  filesystem, a missing device, an unexpected mountpoint, or any other failed
  validation as damaged/unavailable storage. Report the condition but do not
  reject the RAUC update and never erase it automatically.
5. If `p5` has somehow become a RAUC-managed slot, do not mount, format, or
  otherwise modify it for bulk storage. Report the layout conflict and allow
  the update to proceed in degraded mode.

This preflight remains in every future bundle so a customer can update directly
from any old image.

### Idempotent Post-Install Migration

After RAUC writes the inactive rootfs, run `slot-post-install`:

1. Repeat the device and content validation rather than relying only on the
  earlier check.
2. For a legacy filesystem, unmount it if necessary and format it as ext4 with
  label `rave-data` and reduce ext4 reserved blocks to 1 percent. Do not
  rewrite the GPT partition name during OTA.
3. Mount newly initialized storage temporarily and create the managed directory
  tree. If initialized storage is already mounted because it contains the
  running RAUC bundle, operate on that existing mount and leave it mounted.
4. On first initialization, copy the active rootfs's persistent `uploads`
  contents into `storage/uploads` and verify copied file sizes and SHA-256
  values before completing migration. Since the new filesystem is empty, no
  filename conflict policy is needed.
5. Create `payload-cache`, `app-cache`, and `updates` if missing. Never empty an
  initialized directory during an update: `updates` may contain the bundle
  RAUC is currently reading, and future App Store uploads may intentionally
  persist in application storage until installation.
6. After all initialization, copy, and verification steps succeed, write a
  temporary marker, sync it, atomically rename it to `.rave-storage-v1`, and
  sync the containing filesystem. The marker is the final commit record; an
  interrupted migration must never appear complete. Automatic recovery from
  interruption is not required: these systems are normally attended, and the
  supported remedy for an incomplete first migration is to reinstall the
  system.
7. Unmount the data filesystem only if the hook mounted it.
8. Leave the original rootfs files intact. They provide rollback compatibility
   and can be removed only when that slot is later overwritten by RAUC.

The hook must be idempotent. Reinstalling the same or a later bundle should
validate the marker, preserve files already on `p5`, and never reformat an
initialized data filesystem. Once a storage marker exists, do not import or
merge rootfs-local uploads. Any initialization or migration failure must leave
the marker absent, log the failure, and return success to RAUC so the new rootfs
can activate in degraded mode. Damage or absence discovered on a previously
initialized `p5` follows the same non-blocking behavior.

The new rootfs contains the setup service and bind-mount behavior. On first boot
it validates the marker and mounts the already-migrated filesystem before the
menu starts.

Rollback to a pre-migration slot remains bootable because every image retains
its packaged board-test firmware and migration leaves old rootfs data untouched.
Files created on `p5` while running a storage-aware image will not be visible
through old software after rollback, but they remain intact. A legacy rollback
may write new files into its rootfs-local directories; no automatic
cross-slot reconciliation is required, and a later upgrade must not merge those
rootfs-local files into an already initialized `p5`, because rollback behavior
is outside the storage-aware image's control.

Files created in rootfs-local directories while storage is degraded are also
intentionally slot-local. They may become hidden if `p5` mounts successfully
again and are effectively removed when that menu slot is overwritten by a RAUC
upgrade. No automatic import or recovery view is required.

## New NVMe Installations

Change `init-install-efi.sh` so fresh installations create this layout directly:

- Partition 4: `app-a`, ext4 label `app-a`, unchanged.
- Partition 5: `rave-data`, ext4 label `rave-data`.

During installation:

1. Format `p5` with ext4 label `rave-data`, reduce reserved blocks to 1 percent,
   create the managed directories, and commit the marker last.
2. Copy the rootfs to menu slot A as today. The board-test firmware remains
  packaged in that rootfs.
3. Do not create or advertise an application B slot.

The installer must abort before reboot if `p5` initialization fails. A fresh
install has no legacy data to preserve, so failure should be explicit rather
than booting with a partially configured storage filesystem.

## Failure Behavior

- Format `p5` only when no recognized storage marker exists. Legacy `app-b`
  contents are intentionally disposable because that slot has never been used
  by a supported application workflow.
- If `p5` is unavailable or invalid at boot, leave the bind mounts absent and
  use the underlying rootfs directories in degraded mode. Surface the fallback
  and reduced free space clearly in the UI and API.
- Packaged board-test firmware is independent of the data partition. Uploads
  and caches may use the rootfs directories in degraded mode.
- Serialize storage-intensive jobs with one global bulk-operation lock covering
  Lab upload/programming, App Store installation or upload, and network RAUC
  download/install. Return a clear busy response rather than queueing a second
  20 GiB operation.
- Preserve incomplete `.part` and work-file cleanup at boot. Remove only known
  stale temporary patterns; never sweep user uploads.
- Log mount, marker, migration, hash, capacity, and cleanup failures to the
  journal with the partition device and filesystem UUID.
- A missing, damaged, or read-only data filesystem must never block a RAUC
  update. Preserve it without automatic repair or reformatting and let the new
  menu image run in degraded mode.
- Power-loss recovery during first-time formatting or migration is outside the
  automatic recovery scope. Leave the filesystem unmarked/degraded and direct
  the attended operator to reinstall; do not add temporary filesystem labels,
  resumable migration state, or unattended destructive repair.

## Implementation Areas

Expected repository changes:

- `recipes-core/initrdscripts/files/init-install-efi.sh`
  - Rename/reformat the fifth partition as `rave-data` and initialize it.
- `recipes-core/rauc/files/rave2-buddy-bundle-hook.sh`
  - Add the permanent install preflight and guarded, idempotent upload
    migration. Separate fatal storage failures from best-effort Vivado/GRUB
    maintenance.
- `recipes-core/rauc/rave2-buddy-bundle.bb`
  - Declare the bundle `install-check` hook in addition to the rootfs
    `post-install` hook.
- `recipes-core/udev/udev-extraconf/rauc-slots.ignorelist`
  - Ignore explicitly managed `p4` and `p5` as well as RAUC slots.
- `recipes-apps/rave-menu/rave-menu_0.1.bb`
  - Install the storage setup service/script. Keep board-test firmware packaged
    in the rootfs.
- `recipes-apps/rave-menu/files/rave-menu.service`
  - Order the backend after successful storage setup.
- New storage setup script and systemd service under
  `recipes-apps/rave-menu/files/`.
- `recipes-apps/rave-menu/files/routes_actions.py`
  - Report storage availability and capacity consistently for uploads and RAUC
    staging.
- `recipes-apps/rave-menu/files/routes_app_store.py` and
  `recipes-apps/rave-menu/files/scripts/install_app_bundle.sh`
  - Use the shared bulk-operation lock and capacity rules, and preserve cleanup
    behavior.
- `recipes-apps/rave-menu/files/app_slots.py`
  - Replace the misleading reserved-slot-B declaration with an explicit data
    partition declaration or move disk layout constants into a shared module.

## Validation Plan

### Automated and Image-Level Checks

- Shell syntax checks for installer, hook, setup, and programming scripts.
- Unit tests for storage-state classification: empty legacy, initialized,
  non-empty disposable legacy, wrong filesystem, wrong device, mounted
  initialized storage, damaged marker, unexpected mountpoint, and missing
  partition. All damaged/unavailable cases must prove that RAUC remains
  installable.
- Unit tests for capacity calculations, compressed/decompressed peak usage,
  global-lock conflicts, and cleanup after interrupted jobs.
- Inspect built rootfs package manifests to verify that board-test payloads
  remain present in every RAUC rootfs.
- Verify `system.conf` still contains only `rootfs.0` and `rootfs.1`.

### Fresh Installation

- Install to blank NVMe and verify labels, marker, ownership, and mounts.
- Confirm `/var/lib/rave-menu/{uploads,payload-cache,app-cache,updates}` report
  the `p5` filesystem with `findmnt` and `df`.
- Upload and program WIC and OSPI payloads larger than rootfs free space.
- Install streamed and cached App Store bundle types.
- Install a RAUC update while caches contain data and confirm the update bundle
  is staged on `p5`.
- Verify default board-test programming with no explicit paths.

### Upgrade from a Current Installation

- Test with an empty `app-b`, with existing rootfs uploads, and with populated
  cache/work directories.
- Test direct upgrades from each retained legacy image version, without first
  installing an intermediate release.
- Confirm the RAUC hook migrates before reboot and the new slot exposes the same
  upload filenames and content hashes.
- Confirm rollback to the old menu slot still boots and does not alter `p5`.
- Reapply the update and verify migration is idempotent with no data loss.
- Put unexpected content on unmarked `app-b` and verify migration deliberately
  reformats it. Put unexpected content on marked `rave-data` and verify the
  update preserves it.

### Fault Injection

- Remove `p5`, corrupt its marker, use a wrong label, force it read-only, and
  fill it to the reserve threshold.
- Interrupt upload, decompression, App Store install, and RAUC download at
  representative points, then reboot and verify temporary-file cleanup.
- Interrupt first-time storage migration and verify it remains unmarked, boots
  degraded, does not block later RAUC updates, and reports that reinstall is
  required. Automatic migration resume is not required.
- Start competing storage-intensive jobs and verify global-lock enforcement.

## Permanent Compatibility Contract

The change is complete when fresh installs initialize `rave-data`, a single
RAUC update from every supported legacy image safely migrates `p5`, all selected
high-volume mutable paths use `p5`, and rollback remains bootable.

Every later RAUC bundle must preserve the legacy `app-b` preflight and migration
path. New storage schema versions must migrate directly from both legacy
`app-b` and every earlier `.rave-storage-vN` marker; they must not require a
customer to install an intermediate release.