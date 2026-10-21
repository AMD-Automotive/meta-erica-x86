<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC, BM
Maintainer: KC
-->

# App Clone — preserve what is in an application slot

An application slot is a scarce resource. This captures whatever is installed in
one and writes a bundle recipe for it, so a state you spent time getting right
can be backed up, parked while the slot is used for something else, restored
later, handed to a colleague, or attached to a bug report.

```
bundles/app-clone/
  host/capture-app.py         # capture a board -> a ready-to-build recipe directory
  recipe.yaml                 # template for committing a reproducible clone (see below)
  README.md                   # this file
  device/                     # optional home for a local capture (git-ignored)
```

## What a clone guarantees

The bundle reproduces the slot **as it currently is**. It does not care how the
application got there, or what it will be reinstalled onto. The guarantee is that
a reinstalled clone boots with the same arguments as the slot it came from.

Two installed shapes are recognised, and detected for you:

| installed as | what is captured | member(s) |
|---|---|---|
| `x86-rootfs-tar`, `x86-rootfs-ext4`, `custom-build` | the application slot | one `rootfs.tar` |
| `x86-disk-image` | every partition of the app region | one raw member per partition |

## 1. Capture

**Leave the target sitting in Buddy** (the menu). Buddy runs from its own
`menu-a`/`menu-b` partition, which leaves the application idle and safe to copy.
The tool compares the running root against what you asked it to capture and
refuses if you are booted from it.

```sh
./bundles/app-clone/host/capture-app.py 10.0.0.42
```

It reads what the device recorded about its own install, captures the members,
works out how the application boots, and writes a self-contained directory:

```
~/app-captures/app-clone-<source-id>-<stamp>/
  recipe.yaml                 # generated — don't hand-edit, recapture instead
  device/                     # the captured members
```

Everything has a working default:

| flag | env | meaning |
|---|---|---|
| *(first argument)* | `TARGET_HOST` | board IP or hostname |
| `--user` | `TARGET_USER` | login user, needs passwordless sudo (default `amd-edf`) |
| `--out-dir` | `OUT_DIR` | where captures are written (default `~/app-captures`) |
| `--slot-label` | `SLOT_LABEL` | rootfs shape only: partlabel of the slot (default `app-a`) |
| `--exclude-versal` | `EXCLUDE_VERSAL=1` | rootfs shape only: drop `/lib/versal` |
| `--no-shrink` | `NO_SHRINK=1` | disk-image shape only: keep growing ext4 images at their captured filesystem size |

### Sizes

An `app-root` is read with `e2image -ra`, which emits the filesystem rather than
the partition and writes zeros over the free blocks. If the partition has
`grow: true`, the host copy is checked with `e2fsck` and shrunk to its contents
with `resize2fs -M`; the device grows it back to fill its partition. Fixed ext4
filesystems keep their captured size, since older supported installers do not
expand them. The board is never written to — the shrink happens on the capture.

That matters for more than bundle size. Without it a capture is as large as the
slot it came from, so a clone restored into a roomier slot grows to fill it and
the next clone of *that* asks for the larger size again — a floor that ratchets
up every round trip. `--no-shrink` preserves the captured filesystem size;
`e2image` still replaces free blocks with zeros.

The tools have to be able to *read* the filesystem. A VAS3 rootfs carries ext4's
`orphan_file`, which e2fsprogs refuses before 1.47 — an Ubuntu 22.04 host ships
1.46.5 and will not touch it. When that happens the capture borrows the tools
from a `debian:bookworm-slim` container (docker or podman, no root, and it only
ever sees the capture directory). If neither is available the full-size image is
kept and you are told what the clone will end up asking for.

Budget host disk for the raw capture anyway: it is written at full size before
it is shrunk.

A slot installed in the **jumbo** region is cloned as a jumbo bundle: the recipe
carries `type: jumbo`, with `minimum_storage` measured from the members as
captured and `desired_storage` set to the slot the source was running in. The
source bundle's own minimum is not copied — it says only what the app could have
been installed into, which is not what this particular slot now holds. Disk-image
minimums account for fixed partitions and the grow member rounded up to MiB,
then round the total up to whole GiB. No extra padding is added to aligned
members, so an unshrunk 150 GiB layout still asks for 150 GiB.

## 2. Build

The capture directory *is* a recipe directory, so it builds as it stands:

```sh
python3 bundles/_packer/make_bundle.py ~/app-captures/app-clone-vas3-xen-aaos-<stamp>/recipe.yaml
```

## 3. Restore

The App Store's manual-install card takes a URL, an uploaded file, a Dropbox
share or a local path, and can browse USB media — so a clone can be reinstalled
without publishing it anywhere.

## What travels, and what does not

- **Versal images ride along but are not reprogrammed.** They are ordinary files
  on the slot, so a clone restores them; it declares no `versal-*` components, so
  the device does not program the Versal from them. On a same-board restore the
  Versal was already programmed and stays that way. If you want it programmed,
  declare the two images as real `versal-*` components, the way
  [`../pcie-video-tester/recipe.yaml`](../pcie-video-tester/recipe.yaml) does.
- **Host identity and secrets travel with the capture** — ssh host keys,
  `machine-id`, logs, anything the application wrote. Restoring onto a *second*
  board gives you two systems sharing that identity. Fine for backing up and
  restoring one board; worth knowing before cloning across boards.
- **The boot entry is re-pointed, not reinterpreted.** Only the PARTUUID and the
  GRUB device reference change; the hypervisor and kernel arguments are the ones
  that were running.

## Refusals

The tool fails at capture time rather than producing something the device will
reject at install time:

- you are booted from the thing you asked it to capture;
- the board has no completed App Store installation to capture;
- a rootfs capture has no kernel under `./boot`, or holds absolute or `..` paths;
- a captured member is short of the size the device reported;
- the running boot entry contains characters the device's entry adoption refuses;
- the running boot entry matches no entries file on `app-boot`, meaning the slot
  has diverged from whatever installed it. Reproducing that needs `boot.cmdline`,
  which is not implemented yet.

## Refreshing a clone

There is no incremental path — recapture. Each capture is datestamped and gets
its own `id`, `name` and `version`, so captures never collide and the newest is
obvious. Keep the ones you might need to go back to.

## Committing a reproducible clone

A capture is tens of GB, so it can never live in git. The generated recipe points
at the members next to it, which is right for a local backup. To *commit* a clone
instead, host the members and pin them by URL, so anyone can rebuild the same
bundle byte-for-byte without touching the original board. [`recipe.yaml`](recipe.yaml)
in this directory is the template for that.

| `source` type | Where the member lives | Use it when |
|---------------|-------------------------|-------------|
| `file` | next to the recipe, git-ignored | backing up, or iterating locally |
| `url` | a web server / artifact repository, pinned with `sha256` | you want to **commit** it — anyone can rebuild it byte-for-byte |

Upload the **uncompressed** member: the packer needs the raw file, and the
`.rave2app` gzips it on the way out anyway. The `sha256` is not bookkeeping — it
is what makes the committed recipe reproducible, and the packer fails loudly if
whatever sits behind that URL ever changes.
