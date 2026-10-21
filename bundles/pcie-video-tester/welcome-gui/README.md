<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# Welcome GUI

A modular, self-launching health-check + welcome application for the Ubuntu x86
host installed by the PCIe Video Tester bundle.

On startup it runs an ordered pipeline of **checks**. If everything passes a
**Welcome** screen is shown; if a critical check fails an **Error** screen
explains that the Versal side likely needs to be reconfigured and that the user
should reboot into Buddy to re-install the application / Versal image.

## What it checks (in order)

1. **Drivers (one-shot)** — builds and installs the kernel module(s) shipped as
   source under [`drivers/`](drivers/), gated by a marker file so it only runs
   once. See [drivers/README.md](drivers/README.md).
2. **PCIe presence** — the Versal endpoint `10ee:9038` must be enumerated on the
   PCIe bus.
3. **Versal serial** — opens the serial console (default `/dev/ttyUSB1`) and
   queries the Versal Linux image: `/proc/device-tree/model` must be the
   expected board (**OSPI Configuration**) and `/etc/buildinfo` `IMAGE_BASENAME`
   the expected rootfs (**UFS filesystem**). Also reports the build datetime and
   the `meta-erica-edf` / `meta-erica-h-hardware` branch+short-sha.
4. **Kernel modules** — the expected modules are loaded (`lsmod`).

Each check is an independent, reusable unit so the same framework can back other
example apps. Add or remove checks via [`config/welcome.yaml`](config/welcome.yaml).

## Layout

```
welcome-gui/
├── welcome_gui/            Python package
│   ├── __main__.py         entry point (python -m welcome_gui)
│   ├── app.py              orchestration: run checks → route to a screen
│   ├── config.py           load/merge welcome.yaml
│   ├── runner.py           threaded check runner (Qt signals)
│   ├── checks/             modular check implementations
│   │   ├── base.py         Check ABC + CheckResult/Status
│   │   ├── drivers.py      one-shot kmod build/install
│   │   ├── pcie.py         PCIe vendor:device presence
│   │   ├── serial_versal.py serial console image query
│   │   └── kmod.py         loaded-module presence
│   └── ui/                 PySide6 screens (splash/welcome/error)
├── config/welcome.yaml     default configuration
├── drivers/                kernel-module SOURCE drops here (from Brian)
├── packaging/              .desktop autostart entry
└── scripts/                deploy / run / autostart helpers
```

## Develop against the target

The target runs Ubuntu (autologin user `amd`, password `amd`, SSH enabled).

```bash
# one-time: install runtime deps on the target
scripts/bootstrap-target.sh amd@<target-ip>

# push code + run it over SSH (X/Wayland forwarding not needed — it runs in the
# target's own session)
scripts/deploy.sh amd@<target-ip>
scripts/run-on-target.sh amd@<target-ip>

# enable self-launch on login
scripts/install-autostart.sh amd@<target-ip>
```

## Run locally (for UI iteration)

```bash
python -m pip install -r requirements.txt
python -m welcome_gui --config config/welcome.yaml
```

Use `--simulate ok|pcie-fail|serial-fail` to preview screens without hardware.
