<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# The Application Bundle — `.rave2app`

**Specification version: 4.0**

A `.rave2app` file is one self-contained package that a RAVE2 device downloads
and installs. Everything the device needs is inside it: a description of what to
do, the application image itself, and any extra pieces like Versal images or
setup scripts.

Where the [catalog](appstore-catalog-format.md) is the menu, the bundle is the
meal. The catalog only knows enough to list and download your app; the bundle is
the authoritative, self-describing thing that says *how* it installs.

## What a bundle actually is

A bundle is a **gzip-compressed tar archive** — an ordinary `.tar.gz` — that we
name with a `.rave2app` extension:

```
<app-id>-<version>.rave2app
```

for example `pcie-video-tester-2026.06.25.rave2app`.

There's nothing exotic about the container. If you rename it to `.tar.gz` you can
open it with any standard tool. We use a plain gzipped tar on purpose: the device
already has `gzip` and `tar`, and a single whole-file gzip lets the device stream
the contents straight onto storage without ever writing a second full copy to
disk.

Two container rules matter:

1. **The whole archive is gzipped once**, on the outside. We do *not* gzip each
   member individually and then tar them (with the deliberate exception of the
   Versal images — see below).
2. **`appimage.yaml` must be the very first member in the tar.** The device
   reads that descriptor off the front of the stream to decide what to do before
   it commits to anything. If it isn't first, the bundle is rejected.

You can sanity-check both at any time:

```sh
# The descriptor should print without the device reading the whole file:
gzip -dc your-app.tar.gz | tar -xO appimage.yaml

# The first line of the listing should be appimage.yaml:
gzip -dc your-app.tar.gz | tar -t | head -1
```

## What's inside

The members are organised into a few well-known folders. Not all of them are
present in every bundle — most are optional. A typical layout looks like this:

```
appimage.yaml        # REQUIRED, and first. The descriptor (see below).
x86/                 # The x86 application image — exactly one form of it.
  rootfs.tar         #   a root filesystem tree, OR
  rootfs.ext4        #   a raw ext4 image, OR
  build.sh           #   a script that builds/installs the app on the device, OR
  disk/              #   a whole disk image, one raw member per partition
    p1.fat
    p2.ext4
versal/              # OPTIONAL. Versal images, stored compressed (gzip or zstd).
  boot.bin.gz        #   the OSPI boot image — .gz or .zst
  wic.ufs.gz         #   the UFS root image — .gz or .zst
overlay/             # OPTIONAL. File trees to drop onto the installed app.
  app.tar
hooks/               # OPTIONAL. Scripts run during install.
  finalize.sh
LICENSE              # OPTIONAL. Your bundle's own license / notices.
```

A couple of things are worth knowing about how the pieces are stored:

- **The x86 image is stored uncompressed inside the tar.** The outer gzip already
  compresses it for download, and it gets expanded onto the device anyway, so
  compressing it twice would just be wasted work.
- **The Versal images are stored already compressed** — either gzipped
  (`boot.bin.gz`, `wic.ufs.gz`) or zstd-compressed (`boot.bin.zst`,
  `wic.ufs.zst`) — and stay compressed after install to save space on the
  device. This is the one intentional exception to "gzip only on the outside."
- **No member path may be absolute or contain `..`.** A bundle that tries to
  write outside its own tree is rejected. Keep every path relative and tidy.

Every file the descriptor refers to must actually be present in the tar, and the
checksum the descriptor records for it must match. The device cross-checks this
before installing.

## The descriptor: `appimage.yaml`

This is the heart of the bundle — a short YAML document that names your app and
lists its moving parts. The device validates this envelope first, then walks the
`components` list and handles each one according to its `kind`.

Here is a complete, real descriptor. Most of it is optional; the required parts
are called out in the rules that follow.

```yaml
format: rave2-appimage          # constant tag; identifies this as a RAVE2 bundle
format_version: 2               # the descriptor format version

# ---- Identity ----
id: pcie-video-tester           # slug [a-z0-9-]; matches the catalog id
name: PCIe Video Tester         # human-facing name
version: "2026.06.25"           # quote it (see the version note)
vendor: AMD
description: PCIe video test application - x86 host plus Versal images.
type: standard                  # standard (the default) or jumbo

# ---- Optional: refuse to install on too-old devices ----
requires:
  menu_min_version: ""          # require at least this device menu version

# ---- The app-slot filesystem the device creates before installing ----
slot:
  filesystem: ext4              # only ext4 is supported today
  label: app-a                  # filesystem label (drives the boot entry + fstab)
  mkfs_options: "-O ^orphan_file"

# ---- The moving parts, handled in order, dispatched on `kind` ----
components:
  - name: x86-build
    kind: custom-build
    path: x86/build.sh
    sha256: b0deb7311aa1c67c41024589d79d9fe9729cc3af3ba267f934e415a0e104471e

  - name: versal-ospi
    kind: versal-ospi
    path: versal/boot.bin.gz
    sha256: 81eba6c93f1996eca2c882159bd4c991e91e85eac4b16cc0b71c8008f3172a95

  - name: versal-wic
    kind: versal-wic
    path: versal/wic.ufs.gz
    sha256: c071b299fd80c3c542bcbe6b3c6b0aa33606e6ad10433bdea8810c2032c14984

# ---- How the installed app appears in the boot menu ----
boot:
  label: PCIe Video Tester      # boot-menu label (defaults to `name`)
  make_default: false           # make this the default boot target?
  cmdline:                      # required: say how your app boots
    kernel: /boot/vmlinuz       # a symlink is fine; GRUB follows it
    ramdisk: /boot/initrd.img
```

### The envelope fields

| Field | Required | What it's for |
|-------|----------|---------------|
| `format` | yes | Must be exactly `rave2-appimage`. This is how the device knows the file is a RAVE2 bundle at all. |
| `format_version` | yes | The descriptor version. It is `4` today. A device that doesn't support the version you declare will refuse the bundle — which is the point: `2` marks a bundle that declares how it boots, and a device too old to honour that declaration refuses it rather than booting your app on its own arguments; `3` marks a bundle that may declare itself [jumbo](#jumbo-applications); `4` marks a bundle that may contain zstd-compressed `versal-*` members. `4` is a ceiling, not a requirement — it's computed from what's actually in the bundle, so an all-gzip bundle can still declare it. Devices still accept `1`, `2`, and `3`, so older bundles keep installing. Declare the lowest version that covers what you use. |
| `id` | yes | The app slug, lowercase letters/digits/hyphens. Use the **same** `id` as the catalog entry. |
| `name` | yes | The human-facing name. |
| `version` | yes | The release version. Quote it — see the note below. |
| `vendor` | no | Who publishes the app. |
| `description` | no | A short human description. |
| `type` | no | `standard` (the default) or `jumbo`. A jumbo app is one that doesn't fit the standard 50 GiB application slot; see [Jumbo applications](#jumbo-applications). `jumbo` requires `format_version: 3`. |
| `minimum_storage` | jumbo only | Whole GiB. The smallest slot the app can run in; the device refuses to install below it. Required when `type: jumbo`, and rejected on a `standard` bundle. |
| `desired_storage` | jumbo only | Whole GiB, at least `minimum_storage`. The slot size to use when the device has the room. Same requirement/rejection rules as `minimum_storage`. |
| `requires.menu_min_version` | no | If set, the device must be running at least this menu version, or it refuses the bundle. Leave empty (or omit `requires`) to install anywhere. |
| `slot` | no | The filesystem to create for the app. Defaults to `ext4`, label `app-a`, options `-O ^orphan_file` if omitted. Only `ext4` is supported. **If you set `mkfs_options` yourself, keep `-O ^orphan_file`**: the boot menu's GRUB ext driver cannot read a filesystem with that feature enabled, so the app becomes unbootable — and it is on by default in recent `mke2fs`. |
| `components` | yes | A non-empty list of parts (see below). |
| `boot` | no | How the app shows up in the boot menu (see below). |

### The `components` list

This is the ordered list of things the device installs. Every component needs
three fields, and may carry a few optional ones:

| Field | Required | What it's for |
|-------|----------|---------------|
| `kind` | yes | What this part *is*, which decides how the device handles it. See the table below. |
| `path` | yes* | The member inside the tar, e.g. `x86/rootfs.tar`. Must be relative, with no `..`. *Not used by `x86-disk-image`, which names its members inside `layout` instead.* |
| `sha256` | yes* | The SHA-256 of that member **as stored in the tar**. The device verifies it before use. Same exception as `path`. |
| `name` | no | A friendly label for logs. Defaults sensibly. |
| `required` | no | Defaults to `false`. If `true` and the device doesn't understand the `kind`, the install fails instead of skipping the part. This is your lever for "this app genuinely cannot work without this piece." |
| `target` | no | For `overlay` parts, where on the slot to extract (default `/`). |
| `stage` | no | For `overlay` / `hook-script` parts, when to run them: `post-deploy` (default) or `finalize`. |
| `layout` | yes* | Only for `x86-disk-image`: the ordered list of partitions to recreate. See [Shipping a whole disk image](#shipping-a-whole-disk-image). |

The single most important rule: **exactly one** component must be an
*application image* — that is, one of `x86-rootfs-tar`, `x86-rootfs-ext4`,
`custom-build`, or `x86-disk-image`. Everything else is an optional add-on.

### The component kinds

| `kind` | The member it points at | What the device does with it |
|--------|-------------------------|------------------------------|
| `x86-rootfs-tar` | a rootfs tree, e.g. `x86/rootfs.tar` | Formats the app slot and streams the root filesystem straight into it. |
| `x86-rootfs-ext4` | a raw image, e.g. `x86/rootfs.ext4` | Writes the ready-made ext4 image directly onto the slot device. |
| `custom-build` | a script, e.g. `x86/build.sh` | Runs your build script on the device to install the app (for example, to fetch and lay down an Ubuntu system at install time). The bundle ships the *script*, not the rootfs. |
| `x86-disk-image` | several raw images named in `layout` | Recreates your image's partition layout inside the device's app region and writes each partition. The app keeps its own bootloader. See [Shipping a whole disk image](#shipping-a-whole-disk-image). |
| `versal-ospi` | `versal/boot.bin.gz` or `versal/boot.bin.zst` | Persists the (still compressed) OSPI image to the app slot at `/lib/versal/boot.bin.gz` (or `.zst`), then programs the Versal OSPI flash from it. |
| `versal-wic` | `versal/wic.ufs.gz` or `versal/wic.ufs.zst` | Persists the (still compressed) UFS image to the app slot at `/lib/versal/wic.ufs.gz` (or `.zst`), then programs the Versal UFS storage from it. |
| `overlay` | a tar, e.g. `overlay/app.tar` | Extracts a file tree onto the installed slot at `target`. The general-purpose "drop these extra files in place" mechanism. |
| `hook-script` | a script, e.g. `hooks/finalize.sh` | Runs a script against the installed slot at its `stage`, for any custom wiring-up. A non-zero exit fails the install unless the component is marked `required: false`. |

You may include any number of `overlay` and `hook-script` parts; `versal-*`
parts are optional and can be left out entirely for an x86-only app.

#### Where the Versal images land on the slot

The two `versal-*` members are always persisted into the app slot's own root
filesystem under `/lib/versal/`, kept compressed exactly as they ship in the
bundle (gzip or zstd):

```
<app-slot>/lib/versal/
  boot.bin.gz        # from the versal-ospi component (.gz or .zst, matching what shipped)
  wic.ufs.gz         # from the versal-wic component (.gz or .zst, matching what shipped)
  installed.json     # record of what was installed (id, version, per-image sha256s)
```

The device programs the Versal from those persisted files, so they stay on the
slot afterwards (a later re-program uses the same copies without re-downloading
the bundle). The device also writes `<app-slot>/etc/rave2-app.json` — a copy of
`installed.json` — so software running inside the booted app can read exactly
which images are installed. These paths are fixed; a recipe only controls the
in-bundle member path (`produces`), never the on-slot destination.

### Example: an x86 app that ships a Yocto-produced filesystem

The complete descriptor near the top of this document uses a `custom-build`
component — the bundle ships a *script* that assembles the app on the device.
The other common case is the opposite: you already have a finished x86 root
filesystem (for example, one produced by a Yocto image build) and you ship it
inside the bundle directly. That is what `x86-rootfs-tar` and `x86-rootfs-ext4`
are for.

Pick the member form that matches your Yocto `IMAGE_FSTYPES` output:

- A **rootfs tarball** (e.g. `IMAGE_FSTYPES = "tar"`, giving a `*.rootfs.tar`)
  ships as `x86/rootfs.tar` with kind `x86-rootfs-tar`. The device formats the
  app slot and streams the tree into it.
- A **raw ext4 image** (e.g. `IMAGE_FSTYPES = "ext4"`, giving a `*.rootfs.ext4`)
  ships as `x86/rootfs.ext4` with kind `x86-rootfs-ext4`. The device writes the
  ready-made image straight onto the slot device.

Store the image **uncompressed** inside the tar (the outer gzip already
compresses it — see [What's inside](#whats-inside)). The descriptor below ships
a Yocto rootfs tarball as the application image **and** programs a Versal
alongside it — the common shape for a board that pairs an x86 host with a Versal
device:

```yaml
format: rave2-appimage
format_version: 4               # the versal-wic member below ships .zst

id: erica-demo
name: Erica Demo
version: "2026.07.15"
vendor: AMD
description: x86 Yocto host plus Versal OSPI + UFS images.

slot:
  filesystem: ext4
  label: app-a
  mkfs_options: "-O ^orphan_file"

components:
  # The application image: a Yocto-produced x86 root filesystem.
  - name: x86-rootfs
    kind: x86-rootfs-tar          # or x86-rootfs-ext4, pointing at x86/rootfs.ext4
    path: x86/rootfs.tar
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"

  # Versal OSPI boot image. Stored (and kept) gzipped; ships as versal/boot.bin.gz.
  - name: versal-ospi
    kind: versal-ospi
    path: versal/boot.bin.gz
    sha256: "1111111111111111111111111111111111111111111111111111111111111111"

  # Versal UFS root image. May ship gzipped (.gz) or zstd-compressed (.zst);
  # this example uses zstd to show the other accepted suffix.
  - name: versal-wic
    kind: versal-wic
    path: versal/wic.ufs.zst
    sha256: "2222222222222222222222222222222222222222222222222222222222222222"

boot:
  label: Erica Demo
  make_default: false
  cmdline:
    kernel: /boot/bzImage
    ramdisk: /boot/initrd
```

The `versal-*` components are optional — drop them both for an x86-only app, and
you are left with just the `x86-rootfs` component. When present, the device
persists each image to `<app-slot>/lib/versal/` (see
[Where the Versal images land on the slot](#where-the-versal-images-land-on-the-slot))
and programs the Versal from it.

Because the `x86-rootfs` part is a real application image, it must carry its own
kernel under `/boot` (see the next section). A Yocto x86 image typically already
does — a `core-image-*` produced with the Xen/Yocto layout lays down
`/boot/xen.gz` and `/boot/bzImage` for you — so no extra work is usually needed.
You can also add `overlay` or `hook-script` components in the same way if your
app needs post-install wiring-up.

<a id="shipping-a-whole-disk-image"></a>
### Example: an app that ships a whole disk image (`x86-disk-image`)

The three kinds above all hand the device a *root filesystem* (or a recipe for
building one) and let the device's boot menu start it. Some applications don't
fit that shape: they are built and released as a whole disk image — their own
partition table, their own bootloader, their own boot configuration — and what
they do at boot is part of the product. A Xen host that launches a particular
set of domains with particular hypervisor arguments is the usual example.

For those, `x86-disk-image` deploys the image *as an image*. The bundle ships
one raw member per partition, and the device recreates that layout as real
partitions inside a reserved area of its disk, then boots the app with the app's
own boot arguments.

```yaml
components:
  - name: app-disk
    kind: x86-disk-image
    layout:
      - part: app-boot            # the app's boot partition, verbatim
        path: x86/disk/p1.fat
        sha256: "0000000000000000000000000000000000000000000000000000000000000000"
        size_mib: 80
        type: "0700"              # GPT type code: EF00 esp, 0700 basic, 8300 linux
        filesystem: vfat
      - part: app-root            # the app's root, grown to fill the space
        path: x86/disk/p2.ext4
        sha256: "1111111111111111111111111111111111111111111111111111111111111111"
        grow: true
        type: "8300"
        filesystem: ext4

boot:
  label: My Disk App
  entries_cfg: /isolinux/isolinux.cfg   # your GRUB entries, on app-boot
  entry: boot                           # which menuentry to boot
```

Each `layout` entry becomes one partition:

| Field | Required | What it's for |
|-------|----------|---------------|
| `part` | yes | Partition name. Must start with `app-`; the device will only ever delete partitions in that namespace. Name one of them `app-root` — that's the one the device mounts to apply overlays and hooks. |
| `path` | yes | The raw member for this partition, stored uncompressed. |
| `sha256` | yes | Checked as the member is written. |
| `size_mib` | yes* | The size of the partition to create. It may be **larger** than the member you ship — see [Asking for a partition bigger than the image it ships](#more-space-than-the-image). Omit it only on the entry that sets `grow`. |
| `grow` | no | At most one entry may set this. That partition takes all the space left over instead of a fixed `size_mib`, and its filesystem is expanded to match. |
| `type` | no | GPT type code (default `8300`). |
| `filesystem` | no | Informational for `vfat`. `ext4` additionally tells the device it may expand the filesystem to fill its partition. |

Rules worth knowing:

- **Order matters.** Partitions are created in the order you list them, and the
  `grow` entry gets whatever is left, so it is normally last.
- **At most 30 partitions.** The device reserves a fixed block of partition
  indices for applications.
- **Everything is copied verbatim**, which is the point: filesystem labels and
  UUIDs come across unchanged, so anything in your image that refers to storage
  by label or UUID keeps working with no adjustment at all.
- **Your image's own partition table is discarded.** Only the contents of each
  partition are used; the device supplies the table.
- **The region is 51,200 MiB (50 GiB).** Every `size_mib` you declare, plus the
  `grow` entry, has to fit inside that. The device checks and refuses rather
  than truncating. Size the *contents*, not the source image: VAS3 : XEN + AAOS's
  upstream
  boot partition is a 34 GB filesystem holding about 70 MB, and the bundle
  rebuilds it at 1 GiB instead of copying it. (A [jumbo](#jumbo-applications)
  app is sized differently — see [Jumbo and `x86-disk-image`](#jumbo-and-x86-disk-image).)
- **A disk-image install is not streamed.** The other kinds are written as the
  bundle arrives; this one needs the whole `.rave2app` on local disk first,
  because it seeks between members. Your compressed bundle therefore has to fit
  the device's download cache *as well as* the region.

<a id="more-space-than-the-image"></a>
#### Asking for a partition bigger than the image it ships

A member does not have to be the size of the partition it lands in. The device
writes each member raw at the *start* of the partition it created, so when
`size_mib` is larger than the member, the difference is free space at the end of
the partition. What becomes of that space depends on the filesystem:

- **`ext4`** — the device expands the filesystem to fill its partition
  (`e2fsck -fp`, then `resize2fs`) once the member is written. This applies to a
  fixed `size_mib` entry exactly as it does to the `grow` entry: ship a 4 GiB
  image, ask for 16 GiB, and the app boots with a 16 GiB filesystem. If the
  expansion fails the install carries on and the app keeps its shipped size.
- **`vfat`, and anything else** — nothing expands it. The filesystem stays the
  size you built it, and the rest of the partition is unreachable from inside
  the app.

So for a FAT boot partition, **build the member at the size you want**. That is
what VAS3 : XEN + AAOS does: the upstream ISO ships a 34 GB boot filesystem
holding about 70 MB, and `host/make-disk-members.sh` rebuilds it at
`truncate -s 1024M` + `mformat`, with `size_mib: 1024` in the recipe to match.
To give that app a 4 GiB `/boot` you raise both numbers together — the size the
script builds and the `size_mib` it is paired with — and nothing else changes.

Building a filesystem larger than its contents is cheaper than it looks: the
empty tail of a freshly made filesystem is zeros, and the outer gzip squeezes
those down to almost nothing, so a mostly-empty 4 GiB FAT adds megabytes to the
download rather than gigabytes. It does cost install time, because those zeros
are still written out to the partition.

The hard rule is the other direction: **`size_mib` must be at least the size of
the member**. A member too big for the partition you asked for is rejected, not
truncated.

#### What you will still have to fix up

An image built to own a whole disk usually refers to its partitions by
whole-disk index — `root=/dev/nvme0n1p2`, `/dev/sda1 /boot` — and those indices
do not survive the move into a shared disk. Ship a `hook-script` that rewrites
them, preferably into label or UUID forms that don't care where the partitions
land. The installer hands your hook everything it needs:

| Variable | What it is |
|----------|-----------|
| `APP_MNT` | Your mounted `app-root`. |
| `APP_BOOT_DEV` | Device node of your `app-boot` partition. |
| `APP_ROOT_DEV` | Device node of your `app-root` partition. |
| `APP_ROOT_UUID` | Filesystem UUID of `app-root` — prefer this in `root=`. |
| `APP_ROOT_PARTUUID` | GPT PARTUUID of `app-root`. |

Often there is nothing to fix. Adopting an entry already rewrites `root=`, so a
hook is only needed for references the boot entry doesn't cover — an `fstab`
line naming `/dev/sda1`, say. The VAS3 : XEN + AAOS bundle ships no hook at all: its `fstab`
has no `/boot` entry, and `root=` is handled by adoption. Check what is actually
left before writing one.

#### What the boot contract does not cover

The device owns the bootloader and hands off to yours; it does not reproduce
your platform's firmware. If your image's normal boot depends on something
earlier in the chain — a U-Boot script, a device tree assembled at boot, a
particular firmware handing over a configuration table — that part does not come
with it. Your app boots as an *adaptation* of its usual self, not a clone.

In practice this matters less than it sounds: whatever your bootloader can do
from an EFI entry point, it will still do, because it is your bootloader reading
your configuration. But it is worth checking that the configuration your
bootloader reads on the device is the one you think it is.

<a id="jumbo-applications"></a>
## Jumbo applications

Everything above assumes your app fits the standard application slot: a fixed
50 GiB region of the device's disk. Some applications simply don't. A **jumbo
application** is an ordinary bundle in every other respect — same components,
same kinds, same boot contract, same descriptor — except that it declares it
needs more room than the standard slot can give, and says how much.

You mark one with `type`, and declare the two sizes alongside it:

```yaml
format: rave2-appimage
format_version: 3               # jumbo requires 3

id: big-model-workbench
name: Big Model Workbench
version: "2026.09.21"
vendor: AMD
description: Large model workbench - needs more than the standard slot.

type: jumbo                     # standard (the default) or jumbo
minimum_storage: 90             # GiB: below this, don't install at all
desired_storage: 140            # GiB: the size to take when the room is there

slot:
  filesystem: ext4
  label: app-a
  mkfs_options: "-O ^orphan_file"

components:
  - name: x86-rootfs
    kind: x86-rootfs-tar
    path: x86/rootfs.tar
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"

boot:
  label: Big Model Workbench
  cmdline:
    kernel: /boot/bzImage
    ramdisk: /boot/initrd
```

Both sizes are **binary gigabytes** (GiB, 1024³ bytes) and whole numbers: no
unit suffix, no fractions. `desired_storage` must be greater than or equal to
`minimum_storage`, and `minimum_storage` must be greater than `50` — an app that
fits the standard slot is not a jumbo app, and a jumbo bundle asking for 50 GiB
or less is rejected. Declare both fields only on a jumbo bundle; a `standard`
bundle that carries them is rejected too, because it almost always means the
`type` line was forgotten.

### Where the space comes from

A jumbo app does not use the standard 50 GiB application region at all. That
region is discarded for the purposes of a jumbo install — nothing is laid down
in it. Instead the device installs into its **jumbo reservation**: a block of
space it deliberately leaves unpartitioned on the disk, after the bulk-storage
partition and ahead of anything installed there later.

Two consequences matter to you as an author:

- **The reservation is not a fixed size.** It depends on the physical disk in
  the unit and on how the unit was set up — the default reservation is 150 GiB,
  and it can be made smaller or waived entirely. There is no single number every
  device is guaranteed to have, which is exactly why you declare a range rather
  than a size.
- **A device may have no reservation at all.** A unit whose reservation is
  smaller than your `minimum_storage` cannot run your app, and the device says
  so rather than installing something that will run out of space later.
- **An installed app is reclaimed, not worked around.** Installing your app over
  another jumbo app takes its space back, so the size on offer does not shrink
  with each install.

As with a standard app, a device holds one installed application at a time:
installing a jumbo app replaces whatever application is there now, including a
standard one in the 50 GiB region.

### How the device picks the size

At install time the device measures the usable reservation and compares it with
what you declared:

| What it finds | What it does |
|---------------|--------------|
| Less than `minimum_storage` | Refuses the install and reports the shortfall. Nothing is written. |
| At least `minimum_storage`, less than `desired_storage` | Installs, sizing the slot as large as the available space allows. |
| `desired_storage` or more | Installs at `desired_storage`. The remainder is left unallocated. |

So `minimum_storage` is a hard gate and `desired_storage` is a ceiling; between
them, your app gets whatever the device can give. Design for the floor and treat
anything above it as headroom — your app has to be able to start, run, and
update inside `minimum_storage`, because on some units that is all it will get.

What your app does *not* take stays unallocated, and that is deliberate: it is
the space a later Ubuntu install uses. This is also why `desired_storage` should
be an honest number rather than the largest you can imagine — asking for the
whole disk is how a unit ends up with no room for anything else. The operator
can override the size at install time to anything from `minimum_storage` up to
the whole tail, so a user who wants to give your app everything still can; the
value you declare is the default, not a limit on them.

Two practical notes:

- **Sizes are rounded down** to the device's partition alignment, so don't set
  `minimum_storage` to exactly the reservation size you are counting on. Leave
  yourself a GiB of slack.
- **Declare the same numbers in your catalog entry** (see
  [the catalog specification](appstore-catalog-format.md)). The bundle is
  authoritative, but the catalog copy lets the store warn the user *before* a
  multi-gigabyte download that the unit hasn't got the room.

<a id="jumbo-and-x86-disk-image"></a>
### Jumbo and `x86-disk-image`

A jumbo app may ship a whole disk image exactly as described in
[Shipping a whole disk image](#shipping-a-whole-disk-image) — same `layout`
entries, same rules, same 30-partition limit. The one thing that changes is the
size budget: the 51,200 MiB cap is replaced by the slot size the device
negotiated for you.

Since that size isn't known when you author the bundle, size the layout against
the floor: every fixed `size_mib` you declare, added up, must fit inside
`minimum_storage`, and the `grow` entry then absorbs whatever extra the device
was able to give. A layout whose fixed partitions don't fit in `minimum_storage`
is rejected at authoring-check time, not silently truncated on the device.

Remember that a disk-image install is not streamed: the whole `.rave2app` has to
land on the device's local storage first. For a jumbo app that bundle is large
by definition, so check it fits the download cache *as well as* the slot.

#### Worked example: a disk-image app going jumbo

VAS3 : XEN + AAOS is the shape this is built for. It ships two partitions — a
fixed-size FAT boot partition and an ext4 root that takes the rest — so moving
it from the standard slot to a jumbo one is a descriptor change and nothing
else:

```yaml
type: jumbo
minimum_storage: 90             # GiB
desired_storage: 140            # GiB

components:
  - name: app-disk
    kind: x86-disk-image
    layout:
      - part: app-boot
        path: x86/disk/p1.fat
        sha256: "0000000000000000000000000000000000000000000000000000000000000000"
        size_mib: 4096          # a 4 GiB FAT, built at 4 GiB by the host script
        type: "0700"
        filesystem: vfat
      - part: app-root
        path: x86/disk/p2.ext4
        sha256: "1111111111111111111111111111111111111111111111111111111111111111"
        grow: true              # absorbs whatever the device negotiated
        type: "8300"
        filesystem: ext4
```

Reading that against the rules: the only fixed size is `app-boot`'s 4096 MiB,
which fits `minimum_storage` with room to spare, and `app-root` carries `grow`,
so it ends up at whatever slot size the device settled on — 90 GiB on a tight
unit, 140 GiB on a roomy one, without the bundle changing. The boot partition is
FAT, so its *filesystem* has to be built at 4 GiB on the host (see
[Asking for a partition bigger than the image it ships](#more-space-than-the-image));
raising `size_mib` on its own would just leave 3 GiB the app can't reach.

### Choosing between standard and jumbo

Ship standard whenever you can — a standard bundle installs on every device,
and a jumbo one installs only on units with a big enough reservation. Go jumbo
when the application genuinely cannot be made to fit 50 GiB, not to buy
comfortable headroom: the first thing a jumbo declaration does is narrow the set
of devices that will accept your app.

### Example: an Ubuntu app built on the device (`custom-build`)

Sometimes you can't (or don't want to) ship the root filesystem at all — for
example, a stock Ubuntu install you'd rather build fresh on the device than
redistribute. That is what `custom-build` is for: the bundle ships a **script**
instead of an image, and the device runs it at install time to populate the app
slot itself.

The descriptor is almost identical to the Yocto one — only the application-image
component changes:

```yaml
format: rave2-appimage
format_version: 2

id: ubuntu-demo
name: Ubuntu Demo
version: "2026.07.15"
vendor: AMD
description: Ubuntu-based x86 host built on the device at install time.

slot:
  filesystem: ext4
  label: app-a
  mkfs_options: "-O ^orphan_file"

components:
  # The application image: a build script, NOT a rootfs. The device runs it to
  # install Ubuntu straight onto the app slot (nothing proprietary is shipped).
  - name: x86-build
    kind: custom-build
    path: x86/build.sh
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"

  # Optional: pair it with a Versal exactly as in the Yocto example.
  # - name: versal-ospi
  #   kind: versal-ospi
  #   path: versal/boot.bin.gz
  #   sha256: ...

boot:
  label: Ubuntu Demo
  make_default: false
  cmdline:
    kernel: /boot/vmlinuz       # maintained by linux-generic, whatever
    ramdisk: /boot/initrd.img   # kernel version apt actually installed
```

#### What your `build.sh` must do

The device extracts `x86/build.sh` from the bundle, verifies its `sha256`, and
runs it as **root** with the app slot handed over through the environment. Your
script owns the whole application image: it formats the slot, installs a system
onto it (including a kernel under `/boot`), and unmounts cleanly on exit. The
installer wires up the boot menu afterwards — your script must **not** touch the
*device's* GRUB: nothing on the ESP, and not the grubenv.

Writing a GRUB entries file **inside the slot** is fine, and is how a
`custom-build` app declares its boot when the kernel filename is only known once
the package manager has run: write the file, then point `boot.entries_cfg` at it.
Declaring `boot.cmdline` against a stable symlink such as `/boot/vmlinuz` is
usually simpler, and is what the reference Ubuntu app does.

The installer passes these environment variables:

| Variable | What it is |
|----------|-----------|
| `APP_SLOT_DEV` | Block device of the app slot to format and install onto, e.g. `/dev/nvme0n1p4`. |
| `APP_SLOT_LABEL` | Filesystem label to use (from the descriptor `slot.label`, default `app-a`). |
| `APP_SLOT_MKFS_OPTIONS` | Extra `mkfs.ext4` options (from `slot.mkfs_options`). Reuse these so the label and options stay compatible with the menu's GRUB. |
| `BUNDLE` | Path to the bundle on disk, if you need to pull additional members yourself. |

A minimal, generic skeleton:

```sh
#!/bin/sh
set -eu

SLOT_DEV="${APP_SLOT_DEV:?installer must set APP_SLOT_DEV}"
SLOT_LABEL="${APP_SLOT_LABEL:-app-a}"
MKFS_OPTS="${APP_SLOT_MKFS_OPTIONS:--O ^orphan_file}"
MNT="/mnt/app-build"

# 1. Format the app slot with the label + options the installer handed us.
# shellcheck disable=SC2086
mkfs.ext4 -F $MKFS_OPTS -L "$SLOT_LABEL" "$SLOT_DEV"
mkdir -p "$MNT"
mount "$SLOT_DEV" "$MNT"

# 2. Install your system into $MNT. For Ubuntu this is typically: fetch the
#    Ubuntu base rootfs, extract it into $MNT, then chroot in to install a
#    kernel (e.g. linux-generic) plus your packages, a user, and autologin.
#    The key requirement is that $MNT ends up with a bootable kernel under
#    $MNT/boot (bzImage/vmlinuz + initrd) — see the kernel note below.

# 3. Leave the slot clean for the installer (it re-mounts to program Versal
#    and to wire up the boot menu).
sync
umount "$MNT"
```

> The full production script for the PCIe Video Tester lives in the bundle repo
> at `bundles/pcie-video-tester/device/build.sh` and is a good reference for a
> complete Ubuntu build (base rootfs download + checksum, chroot package
> install, user/autologin/fstab, initramfs). Treat it as an example to adapt,
> not a template to copy verbatim.

### A required detail: your app must bring its own kernel

The boot menu's application entry boots the application's **own** kernel from the
app slot — not the device's menu kernel. That keeps the running kernel matched to
the modules your app ships. So your application image must carry a bootable
kernel under `/boot`. Two layouts are recognised automatically:

- **Xen/Yocto style:** `/boot/xen.gz` together with `/boot/bzImage` (plus an
  optional `/boot/initrd`).
- **Bare-metal style:** `/boot/bzImage` (or `/boot/vmlinuz`) plus an optional
  `/boot/initrd` (or `/boot/initrd.img`). This is typical of a `custom-build`
  app such as Ubuntu.

If there's no kernel under `/boot`, the app will not boot: the boot menu does not
lend you its own, and will say so on screen rather than start something that is
not yours.

Whichever layout you use, **name it** — in `boot.cmdline`, or in an entries file
you point `boot.entries_cfg` at. The device no longer guesses by probing the
slot, because guessing is how an app ends up running on arguments nobody chose
for it.

This rule does not apply to `x86-disk-image` apps: they bring their own kernel
and hypervisor, and the device boots them from the paths your `boot` block
names, so where they live is your business.

## How the app gets booted

**Every application must say how it wants to be booted.** A bundle that says
nothing is refused at pack time. There are two ways to say it, and they apply to
every kind of application image — rootfs, disk image, or built on the device.

### Adopt an entry you already ship — `boot.entries_cfg`

If your image already carries a GRUB entries file, point at it and name the
entry. Nothing is translated or re-derived: the entry is taken as written.

```yaml
boot:
  entries_cfg: /isolinux/isolinux.cfg   # path to your GRUB entries file
  entry: boot                           # which menuentry to adopt
```

Three things are changed, and they are exactly the three an installer has to
change when it moves an image onto a disk it did not build:

| what | why |
|---|---|
| `root=...` becomes `root=PARTUUID=<your app-root> rw rootwait quiet` | your image cannot know which partition it landed on |
| `LABEL=...` is removed | the label would resolve against the wrong filesystem |
| `set root=(<your partition>)` is prepended, and any `set root` of your own is dropped | so bare paths resolve against the partition you actually landed on |

Adoption is idempotent: adopting an entry that has already been adopted once
substitutes the PARTUUID and leaves everything else alone, which is what makes a
cloned app installable in turn.

**Where the path points, and what paths inside it mean, depend on your kind.**

| kind | `entries_cfg` is read from | bare paths inside the entry resolve against |
|---|---|---|
| `x86-disk-image` | your `app-boot` partition | your `app-boot` partition, so `/xen` means `/xen` on it |
| everything else | the app slot itself | the app slot, so write `/boot/xen.gz`, **not** `/xen` |

That second row is the one that catches people. A rootfs app has no separate
boot partition, so the prepended `set root` names the slot, and every path in
your entry is slot-relative.

If your image already ships an installer, it very likely writes exactly this
file — check, and point `entries_cfg` at the same one it uses.

### Or declare the arguments — `boot.cmdline`

If you have no entries file, describe the boot instead. This suits an app whose
kernel is installed by a package manager, where the filename is not known when
the recipe is written but a stable symlink is.

```yaml
boot:
  cmdline:
    hypervisor: /boot/xen.gz            # omit for a bare-metal boot
    hypervisor_args: dom0_mem=32G sched=null
    kernel: /boot/vmlinuz
    kernel_args: console=tty0 nomodeset
    ramdisk: /boot/initrd.img           # optional
```

Only `kernel` is required. With a `hypervisor` the device emits `multiboot2`
plus `module2`; without one, `linux` plus `initrd`. Paths are relative to the
partition your app boots from, exactly as in the table above, and GRUB follows
symlinks — so `/boot/vmlinuz` is a good thing to name.

**Do not put `root=` in `kernel_args`.** The installer sets it, because it is
the only party that knows which partition your app landed on; declaring it is
refused. `rauc.slot=` is reserved for the same reason. The installer always
appends `root=PARTUUID=<slot> rw rootwait quiet`, so an app that previously
booted read-only will now boot read-write.

### What the device will not add for you

The device adds `root=` and nothing else. It does **not** merge in the arguments
the boot menu uses for itself, because an app that declares its boot owns its
whole kernel line — having the platform's opinions forced back on top of your
image is how you end up debugging behaviour nobody chose.

The consequence is that a few parameters the menu OS relies on are yours to opt
into. They are reproduced here so you can decide, rather than discover their
absence later:

```
pcie_ports=native pciehp.pciehp_force=1 hpbussize=32 hpmmiosize=512M hpmmioprefsize=1G
```

| parameter | what it does | when to drop it |
|---|---|---|
| `pcie_ports=native` | Linux drives PCIe port services itself rather than leaving them to firmware. | If your image deliberately leaves PCIe services to ACPI. |
| `pciehp.pciehp_force=1` | Forces PCIe hotplug on where firmware does not advertise it. | If you have no hot-pluggable endpoint, or your image manages hotplug itself. |
| `hpbussize=32` | Bus numbers reserved for hotplugged bridges. | If nothing is hotplugged behind a bridge. |
| `hpmmiosize=512M`, `hpmmioprefsize=1G` | MMIO windows reserved for hotplugged devices. | Same. Endpoints needing large BARs may want these *larger*, not absent. |

If your app talks to a device that appears after boot — anything behind a
hot-pluggable bridge — you probably want the whole line. Take it verbatim unless
you know why you are changing it.

A Xen guest additionally wants `console=hvc0 clocksource=xen tsc=unstable
earlyprintk=xen` in its `kernel_args`. That is application policy too: it
describes how *your* dom0 talks to the hypervisor, so it belongs in your
declaration and not in the platform's.

### Rules that apply to both

Arguments are refused, not escaped, if they contain characters that could break
out of the generated GRUB script (`"`, `'`, `` ` ``, `$`, `{`, `}`, backslash),
or if they run past 512 characters or across more than one line.

**`boot.chainload` still parses, but do not reach for it.** On the reference
platform Xen's EFI binary hangs the machine, identically whether GRUB chainloads
it or the firmware launches it from a UEFI boot entry, and there is no console
to diagnose it from. Assume chainloading is unavailable unless you have booted
it yourself on the target.

**Why this matters more than it looks.** An app booted with the *device's*
arguments instead of its own will usually still boot, mount the right root, and
look healthy. On the reference platform, booting VAS3 : XEN + AAOS that way lost
`argo=yes,mac-permissive=true`, and the only visible symptom was one failed
service: `vsock_argo_transport` loaded normally, then `Failed to register argo
ring … (-95)`. Booting is not evidence that the configuration survived — which
is why an app now has to state its own terms rather than inherit the menu's.

**Why the format version moved to 2.** `requires.menu_min_version` looks like it
should cover this, and for devices from this release onwards it does. It cannot
help with older ones: the version comparator they carry is the one this release
fixed, so they read almost any requirement as already satisfied, install the
bundle, and fall back to booting your app with the *menu's* hypervisor and kernel
arguments — quietly, and usually successfully enough to look fine. Refusing an
unfamiliar `format_version` is the one check those devices do perform correctly,
so that is what a declared boot relies on.

### The `boot` block

| Field | Required | What it's for |
|-------|----------|---------------|
| `label` | no | The text shown for your app in the boot menu. Defaults to `name`. |
| `make_default` | no | If `true`, your app becomes the default boot target after install. If `false` (or omitted), the device keeps its current default and your app is simply available to select. |
| `entries_cfg` | yes* | Path to a GRUB entries file the device should adopt an entry from, read after hooks run. On `app-boot` for `x86-disk-image`, inside the slot for every other kind. |
| `entry` | no | Used with `entries_cfg`: which `menuentry` to adopt. Defaults to `boot`. Naming it explicitly matters when the file also contains entries that are known not to work. |
| `cmdline` | yes* | The alternative to `entries_cfg`: declare the boot rather than adopt one. Keys are `kernel` (required), `kernel_args`, `hypervisor`, `hypervisor_args`, `ramdisk`. |
| `chainload` | no | Only for `x86-disk-image`. Path to your own EFI bootloader on `app-boot`, e.g. `/xen.efi`. **Known not to work on the reference platform** — see [How the app gets booted](#how-the-app-gets-booted). Prefer `entries_cfg`. |

\* Exactly one of `entries_cfg` or `cmdline` is required for an application
image. Declaring both is refused, as is declaring neither.

### The same version warning, again

Quote your `version`. A value like `2026_06_25` left unquoted in YAML is read as
the integer `20260625`, and you lose the formatting you intended. Wrapping it in
quotes (`"2026.06.25"` or `"2026_06_25"`) keeps it a string. Use the same version
string here and in the catalog entry.

Quote your `sha256` too, for the same reason. A digest that happens to be all
digits is read as a number, and the validator then reports the rather unhelpful
`missing sha256`.

## How the install runs, end to end

When a user installs your app, the device works through the bundle roughly like
this:

1. Reads `appimage.yaml` off the front of the stream and validates the envelope.
2. Checks that every member the descriptor references is present and that each
   checksum matches.
3. Installs the one application image — formatting the app slot (or running your
   `custom-build` script, which formats it itself; or, for `x86-disk-image`,
   recreating your partition layout and writing each partition).
4. Applies any `overlay` / `hook-script` parts staged `post-deploy`.
5. Persists any `versal-*` images to `<app-slot>/lib/versal/` (kept gzipped) and
   programs the Versal from them.
6. Applies any `hook-script` parts staged `finalize`.
7. Registers the app in the boot menu using your `boot` block, and records that
   the install completed.

If anything fails along the way, the device stops before the point of no return
where it can, and reports the failure rather than leaving you half-installed.

## A short checklist before you publish

- [ ] The file is a gzipped tar named `<app-id>-<version>.rave2app`.
- [ ] `appimage.yaml` is the **first** member.
- [ ] `format: rave2-appimage` and a `format_version` you actually support.
- [ ] `id`, `name`, and a quoted `version` are set, and `id` matches the catalog.
- [ ] Exactly one application-image component is present.
- [ ] Every component's `path` exists in the tar and its `sha256` matches.
- [ ] The application image carries a kernel under `/boot` (or, for
      `x86-disk-image`, on its own boot partition).
- [ ] The `boot` block declares **exactly one** of `entries_cfg` or `cmdline`,
      and any paths in it are relative to the partition your app boots from.
- [ ] For `x86-disk-image`: every `layout` entry names a partition starting
      `app-`, one of them is `app-root`, and at most one sets `grow`.
- [ ] You've recorded the **whole-file** size and SHA-256 for the catalog entry.

## Changelog

- **4.0** — Documents `format_version: 4` (zstd-compressed Versal images) and
  raises the `x86-disk-image` partition limit from ten to 30.
- **3.0** — First tracked version, matching the bundle's current baseline
  `format_version: 3` (jumbo applications).
