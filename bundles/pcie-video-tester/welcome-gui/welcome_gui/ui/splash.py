# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Splash / check-progress screen.

Lists the configured checks and updates each row live as the runner reports
progress. This is the first screen shown on launch.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..checks.base import CheckResult, Status
from .theme import GLYPH_FAIL, GLYPH_OK, GLYPH_PENDING, GLYPH_RUNNING


class _CheckRow(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)

        self.state = QLabel(GLYPH_PENDING)
        self.state.setObjectName("CheckState")
        self.state.setFixedWidth(28)
        self.state.setProperty("kind", "pending")

        self.name = QLabel(title)
        self.name.setObjectName("CheckName")

        self.summary = QLabel("")
        self.summary.setObjectName("Subtitle")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lay.addWidget(self.state)
        lay.addWidget(self.name)
        lay.addStretch(1)
        lay.addWidget(self.summary)

    def set_running(self) -> None:
        self.state.setText(GLYPH_RUNNING)
        self.state.setObjectName("Pending")
        self.summary.setText("checking\u2026")
        self._restyle()

    def set_result(self, result: CheckResult) -> None:
        if result.status is Status.OK:
            self.state.setText(GLYPH_OK)
            self.state.setObjectName("Ok")
        elif result.status is Status.FAIL:
            self.state.setText(GLYPH_FAIL)
            self.state.setObjectName("Fail")
        else:  # SKIP
            self.state.setText("\u2013")
            self.state.setObjectName("Subtitle")
        self.summary.setText(result.summary)
        self._restyle()

    def _restyle(self) -> None:
        # Re-apply the stylesheet so the new objectName-based colour takes effect.
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)


class SplashPage(QWidget):
    """Shows each check and its live state."""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("Page")
        # A bare QWidget only paints its QSS `background` when this attribute is
        # set; without it the desktop palette (light in GNOME) shows through.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(80, 60, 80, 60)
        outer.setSpacing(18)

        head = QLabel(title)
        head.setObjectName("Title")
        sub = QLabel(subtitle)
        sub.setObjectName("Subtitle")
        outer.addWidget(head)
        outer.addWidget(sub)
        outer.addSpacing(10)

        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(10)
        outer.addLayout(self._rows_box)
        outer.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        outer.addWidget(self.progress)

        self._rows: dict[str, _CheckRow] = {}

    def set_checks(self, titles: list[str]) -> None:
        for title in titles:
            row = _CheckRow(title)
            self._rows[title] = row
            self._rows_box.addWidget(row)
        self.progress.setRange(0, max(1, len(titles)))
        self.progress.setValue(0)

    def mark_running(self, title: str) -> None:
        row = self._rows.get(title)
        if row:
            row.set_running()

    def mark_result(self, result: CheckResult) -> None:
        row = self._rows.get(result.name)
        if row:
            row.set_result(result)
        self.progress.setValue(self.progress.value() + 1)
