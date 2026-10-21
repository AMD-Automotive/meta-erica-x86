# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Split-screen console view: Ryzen shell (left) + Versal serial (right).

Shown when the user launches the demo from the welcome screen. Each pane has a
titled header and an embedded live terminal. A back button returns to the
welcome screen and tears the terminals down.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .terminal import PtyBackend, SerialBackend, TerminalWidget


class _ConsolePane(QFrame):
    """A titled card wrapping one terminal."""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("Panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        dot = QLabel("\u25cf")
        dot.setObjectName("PaneDot")
        name = QLabel(title)
        name.setObjectName("PaneTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("CheckSummary")
        header.addWidget(dot)
        header.addWidget(name)
        header.addStretch(1)
        header.addWidget(sub)
        lay.addLayout(header)

        self._term_host = QVBoxLayout()
        self._term_host.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._term_host, 1)
        self._terminal: TerminalWidget | None = None
        self._error: QLabel | None = None

    def attach(self, terminal: TerminalWidget) -> None:
        self._terminal = terminal
        self._term_host.addWidget(terminal)

    def send_input(self, text: str) -> bool:
        """Type ``text`` into this pane's terminal. Returns False if no shell."""
        if self._terminal is None:
            return False
        self._terminal.feed_input(text)
        self._terminal.setFocus()
        return True

    def show_error(self, message: str) -> None:
        self._error = QLabel(message)
        self._error.setObjectName("CheckSummary")
        self._error.setWordWrap(True)
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._term_host.addWidget(self._error)

    def teardown(self) -> None:
        if self._terminal is not None:
            self._terminal.shutdown()
            self._terminal.setParent(None)
            self._terminal.deleteLater()
            self._terminal = None
        if self._error is not None:
            self._error.setParent(None)
            self._error.deleteLater()
            self._error = None


class ConsoleView(QWidget):
    """Two side-by-side live consoles with a back button."""

    def __init__(self, serial_device: str, serial_baud: int = 115200,
                 demo_command: str = "", demo_label: str = "",
                 demo_stop_label: str = "", monitor_command: str = "",
                 endpoint_setup_command: str = "",
                 endpoint_settle_ms: int = 8000) -> None:
        super().__init__()
        self.setObjectName("Page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._serial_device = serial_device
        self._serial_baud = serial_baud
        self._demo_command = demo_command.strip()
        self._demo_label = demo_label or "Start video streams"
        self._demo_stop_label = demo_stop_label or "Stop demo"
        self._monitor_command = monitor_command.strip()
        self._endpoint_setup_command = endpoint_setup_command.strip()
        self._endpoint_settle_ms = max(0, int(endpoint_settle_ms))
        self._demo_proc: QProcess | None = None
        self._demo_running = False
        self._demo_stopping = False
        self._demo_log = "/tmp/welcome-gui-demo.log"
        self._active = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 24)
        outer.setSpacing(16)

        # Header bar.
        bar = QHBoxLayout()
        self._back = QPushButton("\u2190  Back")
        self._back.setObjectName("Ghost")
        title = QLabel("PCIe Video Demo \u2014 Consoles")
        title.setObjectName("Banner")
        self._exit = QPushButton("Exit")
        self._exit.setObjectName("Ghost")
        bar.addWidget(self._back)
        bar.addSpacing(8)
        bar.addWidget(title)
        bar.addStretch(1)
        if self._demo_command:
            self._demo = QPushButton(self._demo_label)
            self._demo.setObjectName("Primary")
            self._demo.clicked.connect(self._on_demo_clicked)  # type: ignore[arg-type]
            bar.addWidget(self._demo)
            bar.addSpacing(8)
        else:
            self._demo = None
        bar.addWidget(self._exit)
        outer.addLayout(bar)

        # The two panes.
        panes = QHBoxLayout()
        panes.setSpacing(18)
        self._ryzen = _ConsolePane("Ryzen shell", "x86 host \u00b7 bash")
        self._versal = _ConsolePane("Versal serial console", serial_device)
        panes.addWidget(self._ryzen, 1)
        panes.addWidget(self._versal, 1)
        outer.addLayout(panes, 1)

    def bind(self, on_back: Callable[[], None]) -> None:
        self._back.clicked.connect(on_back)  # type: ignore[arg-type]

    def bind_exit(self, on_exit: Callable[[], None]) -> None:
        self._exit.clicked.connect(on_exit)  # type: ignore[arg-type]

    # Demo ----------------------------------------------------------------
    def _on_demo_clicked(self) -> None:
        if self._demo_running:
            self._stop_demo()
        else:
            self._start_demo()

    def _start_demo(self) -> None:
        """Bring the endpoint up over serial, then launch the host streams.

        No Ethernet is required between the boards: the Versal endpoint is
        driven over its serial console to start the vivid sources + ep_proxy,
        and only then is the host-side tester launched (in its simplest form,
        with the endpoint already running). The tester runs as a child process
        so its GStreamer windows inherit the GUI's Wayland session (rendered on
        the desktop). The button turns into a Stop control and is disabled
        during teardown to keep a relaunch from racing the shutting-down streams.
        """
        if not self._demo_command or self._demo_running:
            return
        if not self._active:
            self.start()

        self._set_demo_running(True)

        # 1. Bring the endpoint up over the serial console. The setup script
        #    stops any prior ep_proxy and reloads vivid at its start, so this is
        #    safe to re-run; it backgrounds ep_proxy and returns to a prompt.
        if self._endpoint_setup_command:
            self._versal.send_input(self._endpoint_setup_command + "\r")

        # 2. Let the endpoint come up before the host loads the mdb5 driver,
        #    which reads the channel layout from the endpoint at probe time.
        #    (If there's no setup step, launch immediately.)
        delay = self._endpoint_settle_ms if self._endpoint_setup_command else 0
        QTimer.singleShot(delay, self._launch_host_demo)

    def _launch_host_demo(self) -> None:
        """Launch the host-side tester and the per-pane load monitors."""
        if not self._demo_running:
            return  # Stop was pressed during the endpoint settle window.

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.setStandardOutputFile(self._demo_log)
        proc.finished.connect(self._on_demo_finished)  # type: ignore[arg-type]
        program, *arguments = shlex.split(self._demo_command)
        proc.start(program, arguments)
        self._demo_proc = proc

        if self._monitor_command:
            self._ryzen.send_input(self._monitor_command + "\r")
            self._versal.send_input(self._monitor_command + "\r")

    def _stop_demo(self) -> None:
        """Stop the demo: quit the monitors and tear the streams down."""
        # Mark this as a user-initiated stop so the finish handler doesn't treat
        # the resulting exit as a failure and pop an error dialog.
        self._demo_stopping = True
        if self._monitor_command:
            # 'q' quits top/htop, returning each pane to its prompt.
            self._ryzen.send_input("q")
            self._versal.send_input("q")

        if self._demo is not None:
            self._demo.setEnabled(False)
            self._demo.setText("Stopping\u2026")

        proc = self._demo_proc
        if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            # SIGTERM lets the demo's own handler stop GStreamer; force-kill only
            # if it overruns. The endpoint keeps running (a re-run's setup script
            # cleans it up), so no Ethernet is needed to tear it down.
            proc.terminate()
            QTimer.singleShot(8000, lambda p=proc: self._force_kill(p))
        else:
            self._on_demo_finished()

    def _force_kill(self, proc: QProcess) -> None:
        if proc.state() != QProcess.ProcessState.NotRunning:
            proc.kill()

    def _on_demo_finished(self, *args) -> None:
        # QProcess.finished passes (exitCode, exitStatus); a direct call passes
        # nothing. A non-zero exit that we didn't ask for means the streams
        # never came up (e.g. the endpoint proxy wasn't ready, so no video
        # devices) — surface it instead of silently flipping the button back.
        exit_code = args[0] if args else 0
        unexpected = not self._demo_stopping and exit_code != 0
        self._demo_stopping = False
        self._demo_proc = None
        self._set_demo_running(False)
        if unexpected:
            self._show_demo_error(exit_code)

    def _show_demo_error(self, exit_code: int) -> None:
        """Pop a dialog with the tail of the demo log so the failure is visible."""
        detail = ""
        try:
            with open(self._demo_log, encoding="utf-8", errors="replace") as fh:
                lines = [ln.rstrip() for ln in fh if ln.strip()]
            detail = "\n".join(lines[-12:])
        except OSError:
            pass
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Video demo did not start")
        box.setText(
            "The video streams could not be started "
            f"(exit code {exit_code})."
        )
        if detail:
            box.setInformativeText(
                "This usually means the Versal endpoint was not ready. "
                "Re-run the preflight checks (Back) to reboot the endpoint, "
                "then try again."
            )
            box.setDetailedText(detail)
        box.exec()

    def _set_demo_running(self, running: bool) -> None:
        self._demo_running = running
        if self._demo is None:
            return
        self._demo.setEnabled(True)
        self._demo.setText(self._demo_stop_label if running else self._demo_label)

    # Lifecycle -----------------------------------------------------------
    def start(self) -> None:
        """Spawn both terminals. Safe to call when returning to the view."""
        if self._active:
            return
        self._active = True

        try:
            self._ryzen.attach(TerminalWidget(PtyBackend()))
        except Exception as exc:  # noqa: BLE001
            self._ryzen.show_error(f"Could not start shell:\n{exc}")

        try:
            backend = SerialBackend(self._serial_device, self._serial_baud)
            self._versal.attach(TerminalWidget(backend))
        except Exception as exc:  # noqa: BLE001
            self._versal.show_error(
                f"Could not open {self._serial_device}:\n{exc}"
            )

    def stop(self) -> None:
        """Tear both terminals down (called when leaving the view)."""
        if not self._active:
            return
        self._active = False
        proc = self._demo_proc
        self._demo_proc = None
        if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            # Leaving the view stops the demo so streams don't outlive it.
            self._demo_stopping = True
            proc.terminate()
            if not proc.waitForFinished(6000):
                proc.kill()
                proc.waitForFinished(2000)
        self._set_demo_running(False)
        self._ryzen.teardown()
        self._versal.teardown()
