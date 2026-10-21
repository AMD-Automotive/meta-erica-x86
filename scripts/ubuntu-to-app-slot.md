<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# Ubuntu → application slot → bundle

Turn an Ubuntu install on the board's main Ubuntu partition into a RAVE2
application, then package it with `bundles/app-clone`.

Verified end to end on 192.168.0.146, 2026-09-08 (Ubuntu 26.04.1, kernel
7.0.0-31-generic).

## 1. Copy Ubuntu into the app slot

Runs **on the board, sitting in Buddy** — the menu boots from its own partition,
which leaves both the Ubuntu partition and the app slot idle. The script refuses
if you are booted from either.

```sh
scp scripts/ubuntu-to-app-slot.sh amd-edf@<board>:/tmp/
ssh amd-edf@<board>
sudo sh /tmp/ubuntu-to-app-slot.sh --dry-run          # size check, changes nothing
sudo sh /tmp/ubuntu-to-app-slot.sh --exclude /home/amd/yocto
```

Read the dry run before committing. It prints:

```
==> to copy: 9 GiB into a 50 GiB slot
```

If it does not fit, exclude the bulk. On the test board the partition held
237 GiB, but 227 GiB of that was `/home/amd/yocto` — the OS itself is ~9 GiB.
The excluded tree is left untouched on the source partition.

| flag | when to use it |
|---|---|
| `--exclude PATH` | repeatable; omit build trees, datasets, anything not part of the app |
| `--reset-identity` | **if the clone will be restored onto another board** — otherwise it inherits this board's `machine-id` and ssh host keys |
| `--swapfile MIB` | the swap *partition* does not travel; this puts a swapfile inside the slot |
| `--name "Ubuntu 26.04"` | title the menu shows for the app |
| `--source` / `--slot` | non-default partlabels (defaults: `ubuntu`, `app-a`) |
| `--verify-only` | re-check an already-transitioned slot, copy nothing |
| `-y` | skip the confirmation before the slot is reformatted |

The run ends with a cloneability report; every line must read `ok`:

```
==> verifying the slot can be cloned
    ok    installed-app.json kind=custom-build
    ok    kernel resolves under /boot
    ok    app-boot.cfg app_mode=entries
    ok    app-entry.cfg names a kernel (becomes boot.cmdline in the recipe)
    ok    fstab root is LABEL=app-a (survives restore elsewhere)
    ok    no active swap entry in fstab
    ok    no UUID= device references left in fstab
==> the slot is ready for bundles/app-clone/host/capture-app.py
```

## 2. Test the boot

```sh
sudo grub-editenv /boot/efi/EFI/BOOT/grubenv set next_entry=app-a && sudo reboot
```

`next_entry` is one-shot and is cleared before the entry runs, so a hang costs a
single power cycle. The GRUB entry id is **`app-a`**, not `app`.

Do **not** use `boot_default=app` to test — it persists, and a hang loops. The
script's `--make-default` is the deliberate, persistent version.

## 3. Capture and bundle

Reboot back into Buddy first: capture refuses to tar the filesystem it is running
from, and it talks to the menu, not to the app.

```sh
./bundles/app-clone/host/capture-app.py <board>
python3 bundles/_packer/make_bundle.py ~/app-captures/app-clone-ubuntu-<stamp>/recipe.yaml
```

The bundle and its catalog entry land in `bundles/out/`.

Measured on the test board:

| step | time | size |
|---|---|---|
| copy into the slot | ~35 s | 9 GiB at 251 MB/s |
| capture | 36 s | 7997 MiB `rootfs.tar` |
| pack | 9 m 23 s | 3.4 GiB `.rave2app` |

## What the transition fixes, and why

These are the things that only worked because Ubuntu owned the whole disk. Each
one fails *after* boot, or *after* a restore, where it is expensive to diagnose —
there is no serial console on this board.

- **Swap.** The swap partition lives outside the app slot and does not travel
  with a clone. An `fstab` entry for an absent device costs a 90-second systemd
  timeout on every boot, so the line is commented out rather than left to fail.
- **Hibernation.** A stale `resume=` for that swap would stall the initramfs.
  The config is set to `RESUME=none` *and* the boot entry passes `noresume`,
  because the config alone needs a rebuilt initramfs to take effect.
- **Root by label.** `fstab` root becomes `LABEL=app-a`. A label survives being
  restored onto another board; a UUID or PARTUUID does not.
- **`/boot/efi` unmounted.** Keeps Ubuntu's `grub-install` from ever writing to
  the menu's ESP.
- **`-O ^orphan_file`** on the new filesystem — the menu's GRUB ext driver
  cannot read it.
- **xattrs.** The copy uses `rsync -aHAX --numeric-ids`. The Ubuntu rootfs
  carries file capabilities (e.g. `gst-ptp-helper`); `cp -a` or busybox tar drop
  them silently and leave binaries failing for no visible reason.

## Notes

- **Identity travels.** ssh host keys and `machine-id` are copied unless you pass
  `--reset-identity`. Fine for backing up and restoring one board; two boards
  sharing an identity is not.
- **No sshd in the app is not a problem.** If the Ubuntu image has no ssh server,
  you cannot log into it once booted — but capture talks to Buddy, so bundling is
  unaffected. The trip back to the menu has to come from the desktop.
- **Menu version.** A bundle with a declared boot pins
  `menu_min_version: 2026-09-08`. Older menus cannot be gated reliably by that
  field, so do not hand it to a board running a pre-2026-09-08 menu.
