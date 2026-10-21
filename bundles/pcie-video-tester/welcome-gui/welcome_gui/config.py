# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Configuration loading for the Welcome GUI.

Configuration is plain YAML (``config/welcome.yaml``). We deep-merge the file
over built-in defaults so a partial config still works and new keys can be added
without breaking older configs.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "app": {
        "title": "PCIe Video Tester",
        "subtitle": "Welcome",
    },
    # Buttons shown on the welcome screen once all checks pass. Each entry has a
    # `label` and an `action` naming a built-in handler (currently: "console" to
    # open the split-screen Ryzen + Versal consoles).
    "welcome": {
        "actions": [
            {"label": "Launch PCIe video demo", "action": "console"},
        ],
    },
    # The streaming demo launched from a button on the console screen. The host
    # tester runs as a managed child process (so the GStreamer windows inherit
    # the GUI's Wayland session and render on the desktop) while each console
    # pane shows a live monitor. No Ethernet is needed between the boards: the
    # Versal endpoint is brought up over its serial console
    # (`endpoint_setup_command`, with the channel count appended) before the
    # host tester is launched in its simplest form. `endpoint_settle_ms` is how
    # long to wait for the endpoint to come up before loading the host driver.
    "demo": {
        "command": "python3 /opt/welcome-gui/tests/pcie_video_tester.py",
        "channels": 4,
        "endpoint_setup_command": "ep_vivid_setup.sh",
        "endpoint_settle_ms": 8000,
        "button_label": "\u25b6  Start video streams",
        "stop_label": "\u25a0  Stop demo",
        "monitor_command": "htop",
    },
    # Order matters: this is the pipeline order. Each key maps to a check block.
    "pipeline": ["drivers", "pcie", "versal_serial", "kmod"],
    "checks": {
        "drivers": {
            "enabled": True,
            "source_dir": "drivers",
            "marker": "/var/lib/welcome-gui/drivers-installed",
            "build_targets": ["all"],
            "install_command": "sudo make install",
        },
        "pcie": {
            "enabled": True,
            "vendor_device": "10ee:9038",
        },
        "versal_serial": {
            "enabled": True,
            "device": "/dev/ttyUSB1",
            "baud": 115200,
            "timeout_s": 10,
            "model_path": "/proc/device-tree/model",
            "expect_model": "RAVE2 ECP - AMD Versal Gen 2",
            "buildinfo_path": "/etc/buildinfo",
            "expect_image_basename": "erica-appdev-image",
            "report_layers": ["meta-erica-edf", "meta-erica-h-hardware"],
        },
        "kmod": {
            "enabled": True,
            "modules": [],
        },
    },
}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | Path | None) -> dict[str, Any]:
    """Load config from ``path`` merged over :data:`DEFAULTS`.

    A missing path (or ``None``) yields the defaults unchanged.
    """
    if not path:
        return copy.deepcopy(DEFAULTS)

    p = Path(path)
    if not p.is_file():
        return copy.deepcopy(DEFAULTS)

    import yaml

    with p.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config root must be a mapping: {p}")
    return _deep_merge(DEFAULTS, loaded)
