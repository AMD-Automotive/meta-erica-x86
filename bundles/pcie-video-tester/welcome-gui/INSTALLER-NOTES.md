<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# Installer back-annotation notes

Configuration changes we apply **by hand on the target during development** that
must eventually be folded back into the bundle installer
([../device/build.sh](../device/build.sh)) so a fresh install boots into a
working Welcome GUI with no manual steps.

Keep this list authoritative — every manual `apt install`, group change, file
drop, or service tweak we do on `192.168.0.146` goes here.

> **Status (2026-06-26):** Deltas 1–3 are now folded into
> [../device/build.sh](../device/build.sh) — a fresh bundle burn installs the
> GUI runtime packages, adds `amd` to `dialout`, lays the app down at
> `/opt/welcome-gui`, and writes the per-user autostart entry. The app payload
> rides in the bundle as the `welcome-gui` overlay member `x86/welcome-gui.tar`
> (packed by [../device/pack-welcome-gui.sh](../device/pack-welcome-gui.sh)).
> Delta 4 (one-shot driver build) is still pending the driver name.

## Target baseline (observed 2026-06-26)

- Ubuntu 26.04 LTS, kernel `7.0.0-27-generic`, hostname `pcie-video-tester`.
- User `amd` (uid 1000), groups: `amd`, `sudo`. GNOME autologin active.
- Already present: `python3`, `pyserial` 3.5, `PyYAML`, `build-essential`, `bc`,
  `kmod`, `libelf-dev`, kernel headers (from build.sh).
- Hardware: PCIe `10ee:9038` at `0000:62:00.0`. Quad FTDI serial cable
  (`/dev/ttyUSB0..3`, Xilinx `2VE3588`); **Versal Linux console = `/dev/ttyUSB1`**
  (interface 01).

## Deltas to back-annotate into device/build.sh

### 1. Packages (GUI runtime)
`build.sh` installs the kernel/desktop but NOT the GUI toolkit. Add:

- `python3-pyside6.qtwidgets python3-pyside6.qtcore python3-pyside6.qtgui`
  (PySide6 — NOT preinstalled). Fallback: `pip3 install --break-system-packages PySide6`.
- `qt6-wayland` — Qt Wayland platform plugin (`libqwayland*.so`). Without it the
  GUI cannot start in the GNOME Wayland session (`QT_QPA_PLATFORM=wayland`).
- `libxcb-cursor0` — required by the Qt xcb backend (X11 fallback).
- `python3-pyte` (0.8.0, pulls `python3-wcwidth`) — VT terminal emulator used by
  the split-screen console view (Ryzen shell + Versal serial console). **NOT
  preinstalled.** Note: the target has no `pip`, so this must come from apt.
- `python3-ftdi` (pyftdi 0.56.0, pulls `python3-usb`) — FTDI user-space driver
  used to reboot the Versal over the FTDI cable (POR_B reset) when the PCIe
  endpoint is missing. The Debian/Ubuntu package is `python3-ftdi` (NOT
  `python3-pyftdi`, which doesn't exist). Provides `import pyftdi`.
- `python3-serial`, `python3-yaml` — present now, but pin them explicitly in the
  installer so we don't rely on the desktop pulling them in.

> Implemented for dev by [scripts/bootstrap-target.sh](scripts/bootstrap-target.sh).
> **Done in build.sh** — `install_packages()` now apt-installs the PySide6 /
> pyserial / PyYAML / pyte stack plus `qt6-wayland` and `libxcb-cursor0` from
> the universe pocket. (No pip fallback: the target has no pip.)

### 1a. rsync (dev convenience)
- `rsync` is **not** installed on the target. [scripts/deploy.sh](scripts/deploy.sh)
  now streams a `tar | ssh` tarball instead of using rsync (no target rsync
  needed). Nothing to back-annotate unless build.sh wants rsync for other uses.

### 2. User groups — serial access
- `/dev/ttyUSB*` are `root:dialout` mode `0660`; `amd` is **not** in `dialout`,
  so the Welcome GUI cannot open the Versal console as the logged-in user.
- **Action:** add `amd` to `dialout` in build.sh:
  `usermod -aG dialout amd` (chroot: `in_chroot usermod -aG dialout "$NEW_USER"`).
- Alternative considered: run the serial check via sudo (amd has NOPASSWD sudo).
  Prefer the group so the GUI runs unprivileged.
- For dev this is now done automatically by
  [scripts/bootstrap-target.sh](scripts/bootstrap-target.sh) (`usermod -aG dialout`).
- **Done in build.sh** — `configure_system()` runs
  `in_chroot usermod -aG dialout "$NEW_USER"`.

### 3. App deployment
- App installed to `/opt/welcome-gui` (owned root). Launcher
  `/opt/welcome-gui/run-welcome-gui.sh`.
- **Done in build.sh** — `install_welcome_gui()` extracts the bundle member
  `x86/welcome-gui.tar` into `/opt/welcome-gui`, installs
  `/opt/welcome-gui/run-welcome-gui.sh`, and writes
  `~amd/.config/autostart/welcome-gui.desktop` (chowned to `amd`). The payload
  is packed by [../device/pack-welcome-gui.sh](../device/pack-welcome-gui.sh)
  and declared as the `welcome-gui` overlay component in
  [../recipe.yaml](../recipe.yaml).
  (During dev the same is done by [scripts/deploy.sh](scripts/deploy.sh) +
  [scripts/install-autostart.sh](scripts/install-autostart.sh).)

### 4. One-shot driver build/install
- Kernel module sources ship under `/opt/welcome-gui/drivers/`. First GUI launch
  runs `make` + `sudo make install` + `depmod -a`, guarded by
  `/var/lib/welcome-gui/drivers-installed`.
- **Action:** ensure `/var/lib/welcome-gui/` exists and is writable by the build
  step; confirm kernel headers match the *installed* kernel (build.sh installs
  `linux-headers-generic`).
  (`install_welcome_gui()` now creates `/var/lib/welcome-gui`; the driver build
  marker is still TODO once the driver name is known.)
- If the module must load at boot, add it to `/etc/modules-load.d/welcome-gui.conf`
  (TBD once the driver name is known — also feeds `checks.kmod.modules`).

## Still open / to confirm
- Exact serial probe command + expected image fingerprint for the Versal check
  (currently `uname -a` / matches `Linux`).
- Driver name(s) for the `kmod` loaded-module check.
- Whether autostart should be a per-user `.desktop` (current plan) or a systemd
  user service.
