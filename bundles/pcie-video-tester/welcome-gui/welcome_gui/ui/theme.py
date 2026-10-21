# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Shared style + small widget helpers for the UI."""

from __future__ import annotations

# A dark, premium theme so screens look consistent and polished on the target.
STYLE_SHEET = """
* {
    font-family: "Ubuntu", "Inter", "Noto Sans", sans-serif;
    color: #e6edf3;
}
QStackedWidget { background: #0b0f14; }
QWidget#Page { background: #0b0f14; }

/* Typography ---------------------------------------------------------- */
QLabel#Title { color: #ffffff; font-size: 38px; font-weight: 800; }
QLabel#Subtitle { color: #8b97a4; font-size: 17px; }
QLabel#Body { color: #c8d0d8; font-size: 16px; }
QLabel#Banner { color: #ffffff; font-size: 22px; font-weight: 600; }
QLabel#SectionTitle {
    color: #6e7b8a; font-size: 12px; font-weight: 700;
    letter-spacing: 2px; text-transform: uppercase;
}
QLabel#Ok { color: #3fb950; }
QLabel#Fail { color: #f85149; }
QLabel#Pending { color: #d29922; }

/* Success pill -------------------------------------------------------- */
QLabel#Pill {
    color: #3fb950; background: rgba(63, 185, 80, 0.12);
    border: 1px solid rgba(63, 185, 80, 0.35); border-radius: 13px;
    padding: 5px 16px; font-size: 13px; font-weight: 700; letter-spacing: 1px;
}
QLabel#PillFail {
    color: #f85149; background: rgba(248, 81, 73, 0.12);
    border: 1px solid rgba(248, 81, 73, 0.35); border-radius: 13px;
    padding: 5px 16px; font-size: 13px; font-weight: 700; letter-spacing: 1px;
}

/* Cards --------------------------------------------------------------- */
QFrame#Card {
    background: #11161d; border: 1px solid #232b36; border-radius: 16px;
}
QFrame#Panel {
    background: #11161d; border: 1px solid #232b36; border-radius: 16px;
}
QLabel#CardTitle { color: #adb8c4; font-size: 13px; font-weight: 700; letter-spacing: 1px; }

/* Check rows ---------------------------------------------------------- */
QFrame#Row { background: transparent; border: none; }
QFrame#RowDiv { background: transparent; border: none; border-top: 1px solid #1c232d; }
QLabel#CheckName { color: #e6edf3; font-size: 16px; font-weight: 600; }
QLabel#CheckSummary { color: #8b97a4; font-size: 14px; }
QLabel#CheckState { font-size: 17px; font-weight: 700; }

/* Info key/value ------------------------------------------------------ */
QLabel#KeyLabel { color: #8b97a4; font-size: 14px; }
QLabel#ValueLabel { color: #e6edf3; font-size: 15px; font-weight: 600; }
QLabel#ValueMono {
    color: #cdd6e0; font-family: "Ubuntu Mono", "JetBrains Mono", monospace;
    font-size: 14px;
}
QLabel#ValueHero { color: #ffffff; font-size: 18px; font-weight: 700; }

/* Buttons ------------------------------------------------------------- */
QPushButton {
    background: #1f6feb; color: #ffffff; border: none; border-radius: 10px;
    padding: 11px 26px; font-size: 16px; font-weight: 600;
}
QPushButton:hover { background: #388bfd; }
QPushButton:pressed { background: #1858c4; }
QPushButton#Ghost { background: transparent; border: 1px solid #30363d; color: #c8d0d8; }
QPushButton#Ghost:hover { background: #1b212a; }

QProgressBar {
    background: #1c232d; border: none; border-radius: 3px;
}
QProgressBar::chunk { background: #1f6feb; border-radius: 3px; }

QTextEdit#Detail {
    background: #0b0f14; color: #8b97a4; border: 1px solid #232b36;
    border-radius: 10px; font-family: "Ubuntu Mono", monospace; font-size: 13px;
}

/* Split-screen consoles ----------------------------------------------- */
QLabel#PaneTitle { color: #e6edf3; font-size: 15px; font-weight: 700; }
QLabel#PaneDot { color: #3fb950; font-size: 13px; }
QWidget#Terminal {
    background: #0b0f14; border: 1px solid #232b36; border-radius: 10px;
}
"""
# Status glyphs (kept ASCII-safe with a unicode fallback).
GLYPH_OK = "\u2714"       # ✔
GLYPH_FAIL = "\u2716"     # ✖
GLYPH_PENDING = "\u2026"  # …
GLYPH_RUNNING = "\u25B6"  # ▶
GLYPH_SKIP = "\u2013"     # –
