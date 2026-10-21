# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""A lightweight VT terminal widget backed by :mod:`pyte`.

Renders a live terminal (local PTY shell or a serial console) inside a Qt
widget. Reading is driven by a :class:`QSocketNotifier` on the backend file
descriptor, so no extra threads are needed and the UI stays responsive.

The widget is backend-agnostic: any object implementing :class:`_Backend`
(``fileno`` / ``read`` / ``write`` / ``set_size`` / ``close``) can drive it,
which keeps it reusable across example apps.
"""

from __future__ import annotations

import os
from typing import Protocol

import pyte
from PySide6.QtCore import QSocketNotifier, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QKeyEvent,
    QPainter,
    QResizeEvent,
)
from PySide6.QtWidgets import QWidget


# ANSI palette tuned to match the app's dark theme.
_NAMED = {
    "black": "#1c2230",
    "red": "#f85149",
    "green": "#3fb950",
    "brown": "#d29922",
    "yellow": "#d29922",
    "blue": "#58a6ff",
    "magenta": "#bc8cff",
    "cyan": "#39c5cf",
    "white": "#c8d0d8",
    "brightblack": "#6e7b8a",
    "brightred": "#ff7b72",
    "brightgreen": "#56d364",
    "brightyellow": "#e3b341",
    "brightblue": "#79c0ff",
    "brightmagenta": "#d2a8ff",
    "brightcyan": "#56d4dd",
    "brightwhite": "#ffffff",
}
_DEFAULT_FG = QColor("#d6dee8")
_DEFAULT_BG = QColor("#0b0f14")
_CURSOR = QColor("#1f6feb")


class _Backend(Protocol):
    def fileno(self) -> int: ...
    def read(self, size: int) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def set_size(self, rows: int, cols: int) -> None: ...
    def close(self) -> None: ...


def _color(name: str | None, fallback: QColor) -> QColor:
    if not name or name == "default":
        return fallback
    if name in _NAMED:
        return QColor(_NAMED[name])
    # pyte hands back 6-hex strings for 256/true-colour cells.
    if len(name) == 6:
        try:
            return QColor("#" + name)
        except Exception:  # noqa: BLE001
            return fallback
    return fallback


class TerminalWidget(QWidget):
    """Draws a pyte screen and forwards keystrokes to a backend."""

    def __init__(self, backend: _Backend, rows: int = 24, cols: int = 80) -> None:
        super().__init__()
        self.setObjectName("Terminal")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.IBeamCursor)

        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self.setFont(font)
        fm = self.fontMetrics()
        self._cw = max(1, fm.horizontalAdvance("M"))
        self._ch = max(1, fm.height())
        self._ascent = fm.ascent()

        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.ByteStream(self._screen)
        self._backend = backend

        self._notifier = QSocketNotifier(
            backend.fileno(), QSocketNotifier.Type.Read, self
        )
        self._notifier.activated.connect(self._on_readable)
        self._closed = False

    # Reading -------------------------------------------------------------
    def _on_readable(self) -> None:
        try:
            data = self._backend.read(65536)
        except OSError:
            data = b""
        if not data:
            self._on_eof()
            return
        self._stream.feed(data)
        self.update()

    def _on_eof(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._notifier.setEnabled(False)

    # Sizing --------------------------------------------------------------
    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        cols = max(20, self.width() // self._cw)
        rows = max(6, self.height() // self._ch)
        if (cols, rows) != (self._screen.columns, self._screen.lines):
            self._screen.resize(rows, cols)
            try:
                self._backend.set_size(rows, cols)
            except OSError:
                pass
        super().resizeEvent(event)

    # Input ---------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._closed:
            return
        data = _key_to_bytes(event)
        if data:
            try:
                self._backend.write(data)
            except OSError:
                self._on_eof()
        else:
            super().keyPressEvent(event)

    def feed_input(self, text: str) -> None:
        """Programmatically send ``text`` to the backend (e.g. run a command).

        Used to launch the streaming demo from a button so its output appears in
        this terminal and any child windows inherit our session environment.
        Append ``"\\r"`` to submit a command.
        """
        if self._closed:
            return
        try:
            self._backend.write(text.encode("utf-8", "replace"))
        except OSError:
            self._on_eof()

    # Painting ------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), _DEFAULT_BG)
        painter.setFont(self.font())

        buf = self._screen.buffer
        cw, ch, ascent = self._cw, self._ch, self._ascent
        for y in range(self._screen.lines):
            line = buf[y]
            py = y * ch
            for x in range(self._screen.columns):
                cell = line[x]
                ch_data = cell.data or " "
                fg = _color(cell.fg, _DEFAULT_FG)
                bg = _color(cell.bg, _DEFAULT_BG)
                if cell.reverse:
                    fg, bg = bg, fg
                px = x * cw
                if bg != _DEFAULT_BG:
                    painter.fillRect(px, py, cw, ch, bg)
                if ch_data != " ":
                    f = painter.font()
                    if f.bold() != bool(cell.bold):
                        f.setBold(bool(cell.bold))
                        painter.setFont(f)
                    painter.setPen(fg)
                    painter.drawText(px, py + ascent, ch_data)

        # Cursor block.
        if self.hasFocus() and not self._closed:
            cx = self._screen.cursor.x * cw
            cy = self._screen.cursor.y * ch
            painter.fillRect(cx, cy, cw, ch, _CURSOR)
            try:
                under = buf[self._screen.cursor.y][self._screen.cursor.x].data or " "
            except Exception:  # noqa: BLE001
                under = " "
            if under != " ":
                painter.setPen(_DEFAULT_BG)
                painter.drawText(cx, cy + ascent, under)
        painter.end()

    # Lifecycle -----------------------------------------------------------
    def shutdown(self) -> None:
        if self._closed:
            self._closed = True
        self._notifier.setEnabled(False)
        try:
            self._backend.close()
        except Exception:  # noqa: BLE001
            pass


def _key_to_bytes(event: QKeyEvent) -> bytes:
    key = event.key()
    mods = event.modifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

    special = {
        Qt.Key.Key_Return: b"\r",
        Qt.Key.Key_Enter: b"\r",
        Qt.Key.Key_Backspace: b"\x7f",
        Qt.Key.Key_Tab: b"\t",
        Qt.Key.Key_Escape: b"\x1b",
        Qt.Key.Key_Up: b"\x1b[A",
        Qt.Key.Key_Down: b"\x1b[B",
        Qt.Key.Key_Right: b"\x1b[C",
        Qt.Key.Key_Left: b"\x1b[D",
        Qt.Key.Key_Home: b"\x1b[H",
        Qt.Key.Key_End: b"\x1b[F",
        Qt.Key.Key_PageUp: b"\x1b[5~",
        Qt.Key.Key_PageDown: b"\x1b[6~",
        Qt.Key.Key_Delete: b"\x1b[3~",
    }
    if key in special:
        return special[key]

    text = event.text()
    if ctrl and text and len(text) == 1:
        ch = text.lower()
        if "a" <= ch <= "z":
            return bytes([ord(ch) - ord("a") + 1])
    if text:
        return text.encode("utf-8", "replace")
    return b""


# ---- Backends -------------------------------------------------------------

class PtyBackend:
    """A local shell running on a pseudo-terminal (the Ryzen/x86 side)."""

    def __init__(self, command: list[str] | None = None) -> None:
        import fcntl
        import struct
        import subprocess
        import termios

        self._termios = termios
        self._fcntl = fcntl
        self._struct = struct

        self._master, slave = os.openpty()
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        cmd = command or [os.environ.get("SHELL", "/bin/bash")]
        self._proc = subprocess.Popen(
            cmd,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            env=env,
            close_fds=True,
        )
        os.close(slave)
        os.set_blocking(self._master, False)

    def fileno(self) -> int:
        return self._master

    def read(self, size: int) -> bytes:
        return os.read(self._master, size)

    def write(self, data: bytes) -> None:
        os.write(self._master, data)

    def set_size(self, rows: int, cols: int) -> None:
        winsize = self._struct.pack("HHHH", rows, cols, 0, 0)
        self._fcntl.ioctl(self._master, self._termios.TIOCSWINSZ, winsize)

    def close(self) -> None:
        try:
            self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.close(self._master)
        except OSError:
            pass


class SerialBackend:
    """A serial console (the Versal side) exposed as a terminal backend."""

    def __init__(self, device: str, baud: int = 115200) -> None:
        import serial  # pyserial

        self._ser = serial.Serial(device, baud, timeout=0)
        os.set_blocking(self._ser.fileno(), False)
        # Nudge the console to draw a fresh prompt.
        try:
            self._ser.write(b"\r")
        except Exception:  # noqa: BLE001
            pass

    def fileno(self) -> int:
        return self._ser.fileno()

    def read(self, size: int) -> bytes:
        waiting = self._ser.in_waiting
        return self._ser.read(waiting or 1)

    def write(self, data: bytes) -> None:
        self._ser.write(data)

    def set_size(self, rows: int, cols: int) -> None:
        # A serial line has no window size; the remote uses its own default.
        pass

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:  # noqa: BLE001
            pass
