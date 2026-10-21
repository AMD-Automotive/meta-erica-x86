# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Reusable serial-console transport for talking to the Versal side.

Wraps a pyserial connection and runs shell commands over the console, returning
just the command's stdout. Robust against the console echoing the command back
and printing a prompt afterwards: each command is wrapped in unique begin/end
markers and we extract the text between the *output* markers.

The board boots firmware (PLM) -> U-Boot -> Linux, which takes tens of seconds
(DDR init alone is ~20s). Rather than wait passively for it to finish,
:meth:`wait_for_idle` actively drives the board to a usable Linux shell as fast
as possible, mirroring what a human would do at the console:

* It sends a carriage return (Enter). At U-Boot's autoboot countdown / boot
  menu, Enter selects the default and skips the remaining countdown; if that
  drops to the U-Boot prompt (``versal2>``), it types ``boot`` to resume; on an
  already-booted board it just elicits a fresh shell prompt; during firmware /
  kernel boot it is a harmless no-op.
* As soon as a Linux shell prompt appears it returns — no fixed settle wait —
  so the board is talked to the instant it is actually ready.

:meth:`open` still writes nothing (driving only happens in
:meth:`wait_for_idle`), and callers must :meth:`wait_for_idle` before any
:meth:`run`.

This is intentionally UI-agnostic so other example apps can reuse it.
"""

from __future__ import annotations

import re
import secrets
import time

# U-Boot's autoboot countdown / boot menu — the point at which U-Boot is reading
# the console. Pressing Enter here selects the default boot entry and skips the
# rest of the countdown.
_AUTOBOOT_RE = re.compile(r"Hit any key to stop autoboot|U-?Boot Boot Menu")

# The U-Boot command prompt (e.g. ``versal2>``, ``Versal>``, ``ZynqMP>``,
# ``U-Boot>``): a short token ending in ``>`` at the end of the buffer. Linux
# shell prompts end in ``#``/``$`` and contain ``user@host:path``, so they are
# matched separately and never confused with this.
_UBOOT_PROMPT_RE = re.compile(r"(?:^|\n)\s*[\w.\-]+>\s*$")

# A Linux shell prompt of the form ``user@host:path#`` / ``$`` — the signal that
# userspace is up and the console will accept commands.
_PROMPT_RE = re.compile(r"[\w.\-]+@[\w.\-]+:[^\n]*[#$]\s*$")


class SerialConsoleError(RuntimeError):
    """Raised when the console cannot be opened or a command yields no output."""


class SerialConsole:
    """A thin line-oriented client over a serial console.

    Use as a context manager::

        with SerialConsole("/dev/ttyUSB1", 115200) as con:
            model = con.run("cat /proc/device-tree/model")
    """

    def __init__(self, device: str, baud: int = 115200, *,
                 open_timeout: float = 0.5, command_timeout: float = 4.0,
                 nudge_interval_s: float = 2.5, settle_timeout_s: float = 120.0,
                 boot_command: str = "boot") -> None:
        self.device = device
        self.baud = baud
        self.open_timeout = open_timeout
        self.command_timeout = command_timeout
        self.nudge_interval_s = nudge_interval_s
        self.settle_timeout_s = settle_timeout_s
        self.boot_command = boot_command
        self._ser = None

    def __enter__(self) -> "SerialConsole":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        try:
            import serial  # pyserial, imported lazily
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise SerialConsoleError("pyserial is not installed") from exc

        try:
            self._ser = serial.Serial(self.device, self.baud, timeout=self.open_timeout)
        except Exception as exc:  # noqa: BLE001 — surface any open error uniformly
            raise SerialConsoleError(f"cannot open {self.device}: {exc}") from exc

        # IMPORTANT: do NOT write anything here. Driving the board (sending CR /
        # ``boot``) is done only in wait_for_idle(), which knows how to react to
        # what the board prints. Opening is purely to attach; callers must
        # wait_for_idle() before run()ning.

    def wait_for_idle(self, *, quiet_period: float | None = None,
                      timeout: float | None = None) -> bool:
        """Actively drive the board to a ready Linux shell, fast and safely.

        Returns ``True`` as soon as a Linux shell prompt (``user@host:path#``)
        is seen — with no fixed settle wait — so we talk to the board the instant
        it is ready. To get there quickly it behaves like a human at the
        console:

        * On the U-Boot autoboot countdown / boot menu it presses **Enter** to
          select the default boot entry, skipping the rest of the countdown.
        * If that lands at the U-Boot prompt (``versal2>``) it types
          ``boot`` to resume booting.
        * When the console is otherwise silent it sends a lone **Enter** every
          ``nudge_interval`` seconds to elicit a prompt — which both recovers a
          board already stranded at U-Boot and reveals the shell prompt of a
          board that finished booting before we attached. A bare Enter is a
          harmless no-op during firmware/kernel boot.

        Returns ``False`` if no shell prompt appears before ``timeout``
        (``settle_timeout_s``) elapses (e.g. a boot loop).

        The ``quiet_period`` argument is accepted for backward compatibility and
        reused as the nudge interval when given.
        """
        if self._ser is None:
            raise SerialConsoleError("console is not open")

        nudge = self.nudge_interval_s if quiet_period is None else quiet_period
        overall = self.settle_timeout_s if timeout is None else timeout
        deadline = time.monotonic() + overall
        last_nudge = 0.0
        last_boot = 0.0
        tail = ""

        while time.monotonic() < deadline:
            data = self._ser.read(4096)
            now = time.monotonic()
            if data:
                tail = (tail + data.decode("utf-8", "replace"))[-4096:]
                # Linux shell prompt -> we're done; safe to issue commands.
                if _PROMPT_RE.search(tail):
                    self._ser.reset_input_buffer()
                    return True
                # Dropped to the U-Boot prompt -> resume booting.
                if _UBOOT_PROMPT_RE.search(tail) and now - last_boot > 1.0:
                    self._ser.write(f"{self.boot_command}\r".encode())
                    last_boot = now
                    last_nudge = now
                    tail = ""
                    continue
                # Autoboot countdown / boot menu -> pick the default (Enter).
                if _AUTOBOOT_RE.search(tail) and now - last_nudge > 1.0:
                    self._ser.write(b"\r")
                    last_nudge = now
                continue
            # No output: nudge with a lone Enter to elicit a prompt (already-
            # booted board) or interrupt an autoboot we just missed. Harmless
            # during firmware/kernel boot (buffered) and at any prompt.
            if now - last_nudge >= nudge:
                self._ser.write(b"\r")
                last_nudge = now
        # No shell prompt before the deadline — drop backlog so run() is clean.
        self._ser.reset_input_buffer()
        return False

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def run(self, command: str, *, timeout: float | None = None) -> str:
        """Run ``command`` and return its stdout (CRs stripped).

        Raises :class:`SerialConsoleError` if no terminating marker is seen
        within the timeout (e.g. the console is unresponsive).
        """
        if self._ser is None:
            raise SerialConsoleError("console is not open")

        token = secrets.token_hex(4)
        begin, end = f"@B{token}@", f"@E{token}@"
        deadline = time.monotonic() + (timeout or self.command_timeout)

        self._ser.reset_input_buffer()
        self._ser.write(f"echo {begin}; {command}; echo {end}\n".encode())

        buf = ""
        while time.monotonic() < deadline:
            data = self._ser.read(4096)
            if data:
                buf += data.decode("utf-8", errors="replace")
                # Wait until the *output* end-marker (after the echoed command).
                if buf.count(end) >= 2 or (end in buf and begin in buf
                                           and buf.rfind(end) > buf.rfind(begin)):
                    if buf.rfind(end) > buf.rfind(begin):
                        break

        if begin not in buf or end not in buf:
            raise SerialConsoleError(
                f"no response to {command!r} on {self.device} "
                f"(got {len(buf)} bytes)"
            )

        # Content sits after the last begin-marker and before the next end-marker.
        after = buf[buf.rfind(begin) + len(begin):]
        content = after[: after.find(end)]
        return content.replace("\r", "").strip("\n")
