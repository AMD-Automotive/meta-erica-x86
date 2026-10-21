# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Small reusable UI widgets shared across screens."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..checks.base import CheckResult, Status
from .theme import GLYPH_FAIL, GLYPH_OK, GLYPH_SKIP


class ResultRow(QFrame):
    """A static, read-only row showing a finished check's status + summary.

    Used on the welcome/error screens so the preflight results stay visible
    after the checks complete.
    """

    def __init__(self, result: CheckResult) -> None:
        super().__init__()
        self.setObjectName("Row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 10, 4, 10)
        lay.setSpacing(14)

        glyph, obj = _glyph_for(result.status)
        state = QLabel(glyph)
        state.setObjectName(obj)
        state.setProperty("class", "CheckState")
        state.setStyleSheet("font-size: 17px; font-weight: 700;")
        state.setFixedWidth(24)
        state.setAlignment(Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name = QLabel(result.name)
        name.setObjectName("CheckName")
        summary = QLabel(result.summary)
        summary.setObjectName("CheckSummary")
        summary.setWordWrap(True)
        text_col.addWidget(name)
        text_col.addWidget(summary)

        lay.addWidget(state)
        lay.addLayout(text_col, 1)


def _glyph_for(status: Status) -> tuple[str, str]:
    if status is Status.OK:
        return GLYPH_OK, "Ok"
    if status is Status.FAIL:
        return GLYPH_FAIL, "Fail"
    return GLYPH_SKIP, "Subtitle"
