# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Main window: a stacked splash / welcome / error flow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QStackedWidget, QWidget

from ..checks.base import CheckResult, Status
from .console import ConsoleView
from .error import ErrorPage
from .splash import SplashPage
from .welcome import WelcomePage


class MainWindow(QStackedWidget):
    """Owns the three pages and exposes simple navigation hooks."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self._config = config
        app_cfg = config.get("app", {})
        title = str(app_cfg.get("title", "Welcome"))
        subtitle = str(app_cfg.get("subtitle", "Starting up\u2026"))

        self.setWindowTitle(title)
        self._title = title
        self._subtitle = subtitle
        self.splash = SplashPage(title, subtitle)
        self.welcome = WelcomePage(title)
        self.error = ErrorPage()

        serial_cfg = config.get("checks", {}).get("versal_serial", {})
        demo_cfg = config.get("demo", {})
        demo_command = str(demo_cfg.get("command", "")).strip()
        channels = int(demo_cfg.get("channels", 4))
        # No Ethernet between the boards: the host tester runs in its simplest
        # form (endpoint already up) and the Versal is brought up over serial.
        if demo_command and channels:
            demo_command = f"{demo_command} --channels {channels}"
        endpoint_setup = str(demo_cfg.get("endpoint_setup_command", "")).strip()
        if endpoint_setup and channels:
            endpoint_setup = f"{endpoint_setup} {channels}"
        self.console = ConsoleView(
            str(serial_cfg.get("device", "/dev/ttyUSB1")),
            int(serial_cfg.get("baud", 115200)),
            demo_command=demo_command,
            demo_label=str(demo_cfg.get("button_label", "Start video streams")),
            demo_stop_label=str(demo_cfg.get("stop_label", "Stop demo")),
            monitor_command=str(demo_cfg.get("monitor_command", "top")),
            endpoint_setup_command=endpoint_setup,
            endpoint_settle_ms=int(demo_cfg.get("endpoint_settle_ms", 8000)),
        )

        self.addWidget(self.splash)   # index 0
        self.addWidget(self.welcome)  # index 1
        self.addWidget(self.error)    # index 2
        self.addWidget(self.console)  # index 3
        self.setCurrentWidget(self.splash)

        self.console.bind(on_back=self.show_welcome)

    # Navigation -----------------------------------------------------------
    def bind_exit(self, on_exit: Callable[[], None]) -> None:
        """Wire the Exit buttons on the welcome and console pages."""
        self.welcome.bind_exit(on_exit)
        self.console.bind_exit(on_exit)

    def reset_splash(self) -> None:
        """Replace the splash page with a fresh one (clears prior check rows)."""
        old = self.splash
        self.splash = SplashPage(self._title, self._subtitle)
        self.insertWidget(0, self.splash)
        self.removeWidget(old)
        old.deleteLater()

    def show_splash(self) -> None:
        self.setCurrentWidget(self.splash)

    def show_welcome(self) -> None:
        self.console.stop()
        self.setCurrentWidget(self.welcome)

    def show_console(self) -> None:
        """Launch the split-screen Ryzen + Versal console view."""
        self.console.start()
        self.setCurrentWidget(self.console)

    def show_welcome_with(self, results: list[CheckResult]) -> None:
        """Show the welcome screen with the persistent checklist + info."""
        info: dict[str, str] = {}
        for r in results:
            info.update(r.info)
        self.welcome.set_results(results)
        self.welcome.set_info(info)
        self.welcome.set_actions(self._welcome_actions())
        self.setCurrentWidget(self.welcome)

    def _welcome_actions(self) -> list[tuple[str, Callable[[], None]]]:
        """Build the welcome launch buttons from config.

        Each config entry is ``{"label": str, "action": str}``; unknown actions
        are skipped so a typo can't break the screen.
        """
        handlers: dict[str, Callable[[], None]] = {"console": self.show_console}
        actions: list[tuple[str, Callable[[], None]]] = []
        for entry in self._config.get("welcome", {}).get("actions", []):
            label = str(entry.get("label", "")).strip()
            handler = handlers.get(str(entry.get("action", "")))
            if label and handler is not None:
                actions.append((label, handler))
        return actions

    def show_error(self, results: list[CheckResult]) -> None:
        failed = next((r for r in results if r.status is Status.FAIL), None)
        self.error.show_failure(failed)
        self.setCurrentWidget(self.error)
