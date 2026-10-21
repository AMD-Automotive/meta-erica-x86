# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Error screen — shown when a critical check fails.

Explains the most likely cause (Versal not running the expected image) and the
recovery path: reboot into Buddy and re-install the application / Versal image.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..checks.base import CheckResult

_DEFAULT_GUIDANCE = (
    "The Versal side could not be verified. It likely needs to be reconfigured.\n\n"
    "Reboot into Buddy and re-install this application or re-program the Versal "
    "image, then power-cycle and try again."
)


class ErrorPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Page")
        # A bare QWidget only paints its QSS `background` when this attribute is
        # set; without it the desktop palette (light in GNOME) shows through.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(80, 50, 80, 50)
        outer.setSpacing(14)

        self._banner = QLabel("\u2716  Cannot start")
        self._banner.setObjectName("Fail")
        self._banner.setStyleSheet("font-size: 26px; font-weight: 700;")

        self._headline = QLabel("")
        self._headline.setObjectName("Banner")
        self._headline.setWordWrap(True)

        self._guidance = QLabel(_DEFAULT_GUIDANCE)
        self._guidance.setObjectName("Body")
        self._guidance.setWordWrap(True)

        self._detail = QTextEdit()
        self._detail.setObjectName("Detail")
        self._detail.setReadOnly(True)
        self._detail.setMinimumHeight(160)

        outer.addWidget(self._banner)
        outer.addWidget(self._headline)
        outer.addWidget(self._guidance)
        outer.addWidget(self._detail)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._retry = QPushButton("Retry checks")
        self._quit = QPushButton("Quit")
        self._quit.setObjectName("Ghost")
        buttons.addWidget(self._quit)
        buttons.addWidget(self._retry)
        outer.addLayout(buttons)

    def bind(self, on_retry: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._retry.clicked.connect(on_retry)  # type: ignore[arg-type]
        self._quit.clicked.connect(on_quit)  # type: ignore[arg-type]

    def show_failure(self, failed: CheckResult | None) -> None:
        if failed is None:
            self._headline.setText("A required check failed.")
            self._guidance.setText(_DEFAULT_GUIDANCE)
            self._detail.setPlainText("")
            return
        self._headline.setText(f"{failed.name}: {failed.summary}")
        self._guidance.setText(failed.remediation or _DEFAULT_GUIDANCE)
        self._detail.setPlainText(failed.detail or "")
