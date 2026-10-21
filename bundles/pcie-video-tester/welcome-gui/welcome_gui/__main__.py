# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Entry point: ``python -m welcome_gui``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config


def _default_config_path() -> str:
    # config/welcome.yaml sits next to the package root (../config/welcome.yaml).
    here = Path(__file__).resolve().parent.parent
    return str(here / "config" / "welcome.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="welcome_gui", description=__doc__)
    parser.add_argument(
        "--config", default=_default_config_path(),
        help="Path to welcome.yaml (default: bundled config/welcome.yaml).",
    )
    parser.add_argument(
        "--windowed", action="store_true",
        help="Run in a window instead of fullscreen (handy for development).",
    )
    parser.add_argument(
        "--simulate", choices=["ok", "pcie-fail", "serial-fail"],
        help="Render a screen without hardware (UI preview).",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)

    # Imported here so --help works without PySide6 installed.
    from .app import WelcomeApp, simulate

    if args.simulate:
        return simulate(config, args.simulate, fullscreen=not args.windowed)

    return WelcomeApp(config, fullscreen=not args.windowed).run()


if __name__ == "__main__":
    sys.exit(main())
