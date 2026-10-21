# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Welcome screen — shown once all checks pass.

Premium landing page that keeps the completed preflight checklist visible
alongside the Versal configuration it discovered. As examples are added, drop
their launch buttons into :meth:`WelcomePage.set_actions` so the same welcome
screen can front multiple demos.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..checks.base import CheckResult
from .widgets import ResultRow

# Fields rendered larger as the "identity" of the running Versal image.
_HERO_FIELDS = ("OSPI Configuration", "UFS filesystem")


def _panel(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Build a titled card panel; returns (frame, content_layout)."""
    frame = QFrame()
    frame.setObjectName("Panel")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(26, 22, 26, 22)
    outer.setSpacing(6)
    heading = QLabel(title)
    heading.setObjectName("SectionTitle")
    outer.addWidget(heading)
    outer.addSpacing(8)
    body = QVBoxLayout()
    body.setSpacing(0)
    outer.addLayout(body)
    outer.addStretch(1)
    return frame, body


class WelcomePage(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("Page")
        # A bare QWidget only paints its QSS `background` when this attribute is
        # set; without it the desktop palette (light in GNOME) shows through.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        page = QHBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.addStretch(1)

        # Centered, max-width column for a focused, premium feel.
        column = QVBoxLayout()
        column.setContentsMargins(72, 56, 72, 56)
        column.setSpacing(18)
        center = QWidget()
        center.setMaximumWidth(1060)
        center.setLayout(column)
        page.addWidget(center, 6)
        page.addStretch(1)

        # Vertically center the whole block for a balanced, premium feel.
        column.addStretch(1)

        # ── Header ────────────────────────────────────────────────────
        self._pill = QLabel("\u2714  SYSTEM READY")
        self._pill.setObjectName("Pill")
        pill_row = QHBoxLayout()
        pill_row.addWidget(self._pill, 0, Qt.AlignmentFlag.AlignLeft)
        pill_row.addStretch(1)
        column.addLayout(pill_row)

        head = QLabel(title)
        head.setObjectName("Title")
        column.addWidget(head)

        body = QLabel("All preflight checks passed. The target is ready.")
        body.setObjectName("Subtitle")
        body.setWordWrap(True)
        column.addWidget(body)
        column.addSpacing(8)

        # ── Two side-by-side panels ───────────────────────────────────
        panels = QHBoxLayout()
        panels.setSpacing(20)
        checks_frame, self._checks_body = _panel("Preflight checks")
        config_frame, self._config_body = _panel("Versal configuration")
        panels.addWidget(checks_frame, 1)
        panels.addWidget(config_frame, 1)
        column.addLayout(panels)
        column.addSpacing(8)

        # ── Actions ───────────────────────────────────────────────────
        self._actions = QHBoxLayout()
        self._actions.setSpacing(12)
        self._exit = QPushButton("Exit")
        self._exit.setObjectName("Ghost")
        self._actions.addWidget(self._exit)
        self._actions.addStretch(1)
        column.addLayout(self._actions)
        column.addStretch(1)

        self._launch_buttons: list[QPushButton] = []

    def bind_exit(self, on_exit: Callable[[], None]) -> None:
        self._exit.clicked.connect(on_exit)  # type: ignore[arg-type]

    # Population -----------------------------------------------------------
    def set_results(self, results: list[CheckResult]) -> None:
        """Render the finished checks as a persistent checklist."""
        _clear(self._checks_body)
        first = True
        for result in results:
            if not first:
                div = QFrame()
                div.setObjectName("RowDiv")
                div.setFixedHeight(1)
                self._checks_body.addWidget(div)
            self._checks_body.addWidget(ResultRow(result))
            first = False

    def set_info(self, items: dict[str, str]) -> None:
        """Render the discovered Versal configuration as labelled rows."""
        _clear(self._config_body)
        if not items:
            empty = QLabel("No configuration reported.")
            empty.setObjectName("CheckSummary")
            self._config_body.addWidget(empty)
            return

        # Hero fields first (OSPI / UFS), emphasised; then the rest in a grid.
        for key in _HERO_FIELDS:
            if key in items:
                self._config_body.addWidget(_hero(key, items[key]))
                self._config_body.addSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        row = 0
        for label, value in items.items():
            if label in _HERO_FIELDS:
                continue
            key = QLabel(label)
            key.setObjectName("KeyLabel")
            val = QLabel(str(value))
            val.setObjectName("ValueMono" if _looks_mono(str(value)) else "ValueLabel")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setWordWrap(True)
            grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(val, row, 1, Qt.AlignmentFlag.AlignLeft)
            row += 1
        if row:
            self._config_body.addSpacing(4)
            self._config_body.addLayout(grid)

    def set_actions(self, actions: list[tuple[str, Callable[[], None]]]) -> None:
        """Add launch buttons for downstream demos. ``actions`` is a list of
        ``(label, callback)`` pairs. The persistent Exit button is preserved."""
        # Remove only the previously-added launch buttons; keep Exit + stretch.
        for btn in self._launch_buttons:
            self._actions.removeWidget(btn)
            btn.deleteLater()
        self._launch_buttons.clear()
        for label, callback in actions:
            btn = QPushButton(label)
            btn.clicked.connect(callback)  # type: ignore[arg-type]
            self._actions.addWidget(btn)
            self._launch_buttons.append(btn)


def _hero(label: str, value: str) -> QWidget:
    box = QFrame()
    box.setObjectName("Row")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 4, 0, 6)
    lay.setSpacing(2)
    cap = QLabel(label)
    cap.setObjectName("KeyLabel")
    val = QLabel(value)
    val.setObjectName("ValueHero")
    val.setWordWrap(True)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(cap)
    lay.addWidget(val)
    return box


def _looks_mono(value: str) -> bool:
    # Layer revisions / shas read better in a monospace font.
    return ":" in value and any(c.isdigit() for c in value)


def _clear(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()
        elif item.layout():
            _clear(item.layout())
