<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# Visual streaming demo

This folder holds the **host-side visual demo** for the MDB5 PCIe video bridge.
It runs on the x86 root-complex host: it loads the `mdb5_v4l2` driver, discovers
the `/dev/video*` nodes it exposes, and tiles the live streams into a single
GStreamer window. With `--endpoint_host` it also SSHes into the Versal endpoint,
spins up `vivid` capture sources, and starts `ep_proxy` automatically.

These files are the demo the Welcome GUI will eventually **automate** (kick off
the streams as a one-click check). For now they are simply staged onto the
target so they are present and runnable by hand.

The host-side demo is the single source of truth in the meta-layer test recipe
at `recipes-tests/pcie-video-tester/files/pcie_video_tester.py`. It is copied
into this folder **at bundle pack time** by
[../../device/pack-welcome-gui.sh](../../device/pack-welcome-gui.sh). Only this
`README.md` is kept in git here — the demo file appears in the packed bundle,
not in the source tree, so the two copies can never drift.

The endpoint-side pieces (`ep_vivid_setup.sh` + `mdb5_vivid_channels.json`) are
not staged here: they ship pre-installed on the Versal rootfs via
`meta-erica-edf` (`ep_vivid_setup.sh` at `/usr/bin/`,
`mdb5_vivid_channels.json` at `/etc/`). The tester resolves and runs the script
in place over SSH; pass `--endpoint-script` to override its path.

On the installed x86 host the demo lands at `/opt/welcome-gui/tests/`.

## Layout (in the packed bundle)

```
tests/
├── README.md                   (this file)
└── pcie_video_tester.py        ← host-side visual tester (loads driver, tiles streams)
```

## Running by hand

```sh
# ep_proxy already running on the endpoint:
python3 /opt/welcome-gui/tests/pcie_video_tester.py

# bring the endpoint up automatically (vivid + ep_proxy over SSH):
python3 /opt/welcome-gui/tests/pcie_video_tester.py --endpoint_host root@<versal-ip>
```

Runtime prerequisites on the host (installed by
[../../device/build.sh](../../device/build.sh)): `v4l-utils`, GStreamer
(`gst-launch-1.0` + the base/good plugins), and an SSH client for the
`--endpoint_host` path.
