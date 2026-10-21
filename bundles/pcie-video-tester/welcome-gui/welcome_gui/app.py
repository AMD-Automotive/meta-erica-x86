# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Application orchestration: wire the check runner to the UI flow."""

from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .checks.base import CheckResult, Status
from .runner import CheckRunner, build_pipeline
from .ui.theme import STYLE_SHEET
from .ui.window import MainWindow


class WelcomeApp:
    """Owns the QApplication, the window and the (re)startable check runner."""

    def __init__(self, config: dict[str, Any], *, fullscreen: bool = True) -> None:
        self.config = config
        self._qapp = QApplication.instance() or QApplication(sys.argv)
        # Use Fusion so widget palettes don't inherit the desktop GTK theme
        # (which is light); our QSS then renders consistently dark everywhere.
        try:
            self._qapp.setStyle("Fusion")
        except Exception:  # noqa: BLE001 — style is best-effort
            pass
        self._qapp.setStyleSheet(STYLE_SHEET)

        self.window = MainWindow(config)
        if fullscreen:
            self.window.showFullScreen()
        else:
            self.window.resize(900, 640)
            self.window.show()

        self.window.error.bind(on_retry=self.start_checks, on_quit=self._qapp.quit)
        self.window.bind_exit(self._qapp.quit)
        self._runner: CheckRunner | None = None

    # Lifecycle ------------------------------------------------------------
    def run(self) -> int:
        self.start_checks()
        return self._qapp.exec()

    def start_checks(self) -> None:
        """(Re)build the pipeline and run it, resetting the splash screen."""
        self._teardown_runner()

        checks = build_pipeline(self.config)
        self.window.reset_splash()
        self.window.splash.set_checks([c.title for c in checks])
        self.window.show_splash()

        self._runner = CheckRunner(checks)
        self._runner.check_started.connect(self.window.splash.mark_running)
        self._runner.check_finished.connect(self.window.splash.mark_result)
        self._runner.finished_all.connect(self._on_finished)
        self._runner.start()

    def _on_finished(self, overall_ok: bool, results: list[CheckResult]) -> None:
        if overall_ok:
            self.window.show_welcome_with(results)
        else:
            self.window.show_error(results)

    def _teardown_runner(self) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._runner = None


# ---- Simulation support (preview screens without hardware) ----------------

def simulate(config: dict[str, Any], mode: str, *, fullscreen: bool = False) -> int:
    """Render a screen directly for UI iteration. ``mode`` is one of
    ``ok`` / ``pcie-fail`` / ``serial-fail``."""
    app = WelcomeApp(config, fullscreen=fullscreen)
    title = str(config.get("app", {}).get("title", "Welcome"))
    app.window.splash.set_checks([title])

    if mode == "ok":
        app.window.show_welcome_with([
            CheckResult(
                "Versal serial console", Status.OK,
                "RAVE2 ECP - AMD Versal Gen 2 / erica-appdev-image",
                info={
                    "OSPI Configuration": "RAVE2 ECP - AMD Versal Gen 2",
                    "UFS filesystem": "erica-appdev-image",
                    "Distro version": "25.11+development-fb2d654a",
                    "Build datetime": "20260623164020",
                    "meta-erica-edf": "main:63bcd18",
                    "meta-erica-h-hardware": "main:9d113ab",
                },
            )
        ])
    elif mode == "pcie-fail":
        app.window.show_error([
            CheckResult(
                "PCIe endpoint", Status.FAIL, "PCIe device 10ee:9038 not found",
                detail="Scanned /sys/bus/pci/devices for the Versal endpoint.",
                remediation=(
                    "The Versal PCIe endpoint was not found. Reboot into Buddy and "
                    "re-install this application / re-program the Versal image."
                ),
            )
        ])
    elif mode == "serial-fail":
        app.window.show_error([
            CheckResult(
                "Versal serial console", Status.FAIL,
                "Expected pattern not seen on /dev/ttyUSB0",
                detail="Sent: 'uname -a'\nReceived:\n(no response)",
                remediation=(
                    "Could not confirm the expected image over serial. Reboot into "
                    "Buddy and re-program the Versal image."
                ),
            )
        ])
    else:
        raise SystemExit(f"Unknown simulate mode: {mode}")

    return app._qapp.exec()
