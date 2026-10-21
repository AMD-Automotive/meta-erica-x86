<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: BW
Maintainer: BW
-->

# pcie-camera-passthrough-simple

Camera → PCIe → display passthrough demo for the MDB5 video bridge.

Cameras on a Versal Gen 2 endpoint are streamed over PCIe via the `mdb5_v4l2`
host driver and displayed on the x86 root-complex host. Two display paths are
provided:

| Mode     | How it displays                                                        |
|----------|------------------------------------------------------------------------|
| `mmap`   | GStreamer `compositor` tiled into a window (Wayland/X, autovideosink).  |
| `dmabuf` | Zero-copy V4L2 → amdgpu DRM/KMS; one overlay plane per camera, full-screen takeover. |

## Camera configs

| config | channels | devices                       |
|--------|----------|-------------------------------|
| 0      | 1        | `/dev/video0`                 |
| 1      | 4        | `/dev/video0` … `/dev/video3` |
| 2      | 5        | `/dev/video0` … `/dev/video4` |

Cameras 0–3 run **1920×1536** (mmap) / **1792×1536** (dmabuf); camera 4 is
always **3840×2160**. All channels are NV12.

The `mdb5_v4l2` format is static — the resolution is set on the **endpoint** by
`init_cameras.sh`, not by this app. The endpoint always brings up all 5 channels;
the **mode** selects the resolution and the **config** is passed through too. The
app then opens just the channels the config variant needs.

## Layout

Both modes tile identically: `cols = ceil(sqrt(n))`, `rows = ceil(n/cols)`
(1→1×1, 4→2×2, 5→3×2). Each source is aspect-fit and centered in its tile.

## Files

| File                  | Role                                                            |
|-----------------------|----------------------------------------------------------------|
| `camera_simple.py`    | Top-level orchestrator / entry point.                          |
| `init.py`             | Endpoint SSH, driver install, dmabuf build, device discovery.  |
| `camera_mmap.py`      | Standalone GStreamer tiled display (mmap).                     |
| `camera_dmabuf.c`     | Zero-copy multi-camera DRM/KMS display (dmabuf).              |
| `Makefile`            | Builds `camera_dmabuf` (kernel uAPI headers only, no libdrm).  |
| `install_mdb5_drv.sh` | **Placeholder** — build + load `mdb5_v4l2` locally. Replace with the real script. |

## Flow

1. SSH into the endpoint and run `init_cameras.sh <mode> <config>`.
2. Run `install_mdb5_drv.sh` locally to build + load `mdb5_v4l2`.
3. Discover the expected `/dev/videoN` nodes (verified as `mdb5_v4l2`).
4. Launch the `mmap` or `dmabuf` display path.

## Usage

```sh
# 4-channel tiled window (mmap)
camera_simple.py --mode mmap --config 1

# 5-channel zero-copy takeover on amdgpu (dmabuf)
camera_simple.py --mode dmabuf --config 2

# Custom endpoint; skip bring-up/install if already done
camera_simple.py --mode dmabuf --config 1 \
    --endpoint_host root@192.168.1.222 --no-endpoint --no-install
```

Default endpoint is `root@192.168.1.222`. `Ctrl+C` stops cleanly.

## Contracts (external scripts)

- **`init_cameras.sh`** ships on the endpoint (in `/usr/bin`) and is invoked as
  `init_cameras.sh <mmap|dmabuf> <0|1|2>`. It always brings up all 5 channels;
  the mode selects the resolution and the config is passed through.
- **`install_mdb5_drv.sh`** ships in this recipe, runs locally, and must exit 0
  with `mdb5_v4l2` loaded. The bundled copy is a placeholder — replace it.

## Notes

- `dmabuf` mode needs one NV12 overlay plane per camera. amdgpu has a limited
  number of planes per CRTC; if there are fewer planes than cameras (the 5ch
  case is the risk), `camera_dmabuf` warns and displays as many channels as it
  has planes for.
- `dmabuf` mode requires IOMMU enabled so the PCIe DMA engine can write into GPU
  GTT memory.
