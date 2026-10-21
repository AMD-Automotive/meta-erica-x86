<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# Kernel module sources

This folder holds the kernel-module **source code** the Welcome GUI builds and
installs **once** on first launch (the `drivers` check in
[../config/welcome.yaml](../config/welcome.yaml)), after which it records a
marker file so it never rebuilds unless forced.

The driver is **mdb5-v4l2** (the MDB5 PCIe video-bridge V4L2 host driver, module
`mdb5_v4l2`, which binds the Versal endpoint `10ee:9038`). Its sources are the
single source of truth in the meta-layer recipe at
`recipes-modules/mdb5-v4l2/files/` and are copied into this folder **at bundle
pack time** by [../../device/pack-welcome-gui.sh](../../device/pack-welcome-gui.sh).
Only this `README.md` is kept in git here — the `.c`/`.h`/`Makefile` appear in
the packed bundle, not in the source tree, so the two copies can never drift.

## Layout (in the packed bundle)

```
drivers/
├── README.md          (this file)
├── Makefile           ← kbuild Makefile, default `all` target builds the .ko
├── <driver>.c
└── <driver>.h
```

A typical out-of-tree kbuild `Makefile`:

```make
obj-m += mydriver.o
KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

install:
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install
	depmod -a

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

## How the one-shot install works

1. `drivers` check runs `make all` (configurable via `build_targets`).
2. Then runs the `install_command` (default `sudo make install`).
3. Runs `depmod -a`.
4. Writes the marker at `/var/lib/welcome-gui/drivers-installed`.

The module is **built and installed but deliberately not loaded.** The
mdb5_v4l2 driver latches its channel layout from the endpoint mailbox at probe
time and never re-syncs, so it must only be loaded on demand (by the e2e test
harness, after `ep_proxy` has stamped the mailbox) — never auto-loaded at boot.
The OS image ships `/etc/modprobe.d/mdb5-v4l2.conf` (`blacklist mdb5_v4l2`,
written by [device/build.sh](../../device/build.sh)) which suppresses udev
modalias coldplug auto-load; an explicit `modprobe mdb5_v4l2` still works.

On later launches the check sees the marker and short-circuits to OK. To force a
rebuild, delete the marker or set `force: true` under `checks.drivers`.

## Verifying the module

`checks.kmod.modules` lists `mdb5_v4l2`. The `kmod` check confirms the built
`.ko` is present in the kernel module tree **by name** (a filesystem lookup) —
it does not load the module and does not require it to be loaded.

## Build prerequisites on the target

The bundle's [device/build.sh](../../device/build.sh) already installs
`build-essential`, `bc`, `kmod`, `libelf-dev`, `linux-headers-generic` and
`initramfs-tools`, which cover out-of-tree module builds.
