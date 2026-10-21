#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Versal boot/serial timeline diagnostic.

Resets the Versal over the FTDI cable, then characterizes what happens on the
serial console and the PCIe bus so we can understand the "stuck in U-Boot"
race. It answers three questions:

  1. Timeline: when does U-Boot's autoboot prompt appear after a POR reset, how
     long are the silent gaps during boot, and when does the console settle?
  2. PCIe-vs-console race: does the Versal enumerate on PCIe *before* the serial
     console goes idle (i.e. while still in U-Boot / early Linux)? If so, the
     pipeline's PCIe check passes and the serial check opens the port mid-boot.
  3. Does merely OPENING the port stop autoboot? This run opens the port the way
     the app does (pyserial defaults: DTR/RTS asserted) and writes NOTHING. If
     the board nonetheless never reaches a Linux prompt and stays at U-Boot,
     then the act of opening (DTR/RTS toggle or line glitch) is the culprit.

Usage (run as root on the target):
  sudo python3 serial_diag.py [--mode dtr-default|dtr-low|no-open] [--secs 120]

  --mode dtr-default  open exactly like the app (serial.Serial defaults)  [default]
  --mode dtr-low      open with DTR/RTS deasserted before the line activates
  --mode no-open      reset only; never open the port (control: pure boot)

Nothing is ever written to the console — this tool only listens.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time

VENDOR = "0x10ee"
DEVICE = "0x9038"
DEV = "/dev/ttyUSB1"
BAUD = 115200
RECONFIG = "/opt/welcome-gui/welcome_gui/versal/reconfigure_versal.py"

MARKERS = [
    "U-Boot", "Hit any key", "stop autoboot", "Starting kernel",
    "Linux version", "systemd", "login:", "amd-ecp", "root@",
]


def pcie_present() -> str | None:
    for d in sorted(glob.glob("/sys/bus/pci/devices/*")):
        try:
            with open(os.path.join(d, "vendor")) as f:
                v = f.read().strip()
            with open(os.path.join(d, "device")) as f:
                dv = f.read().strip()
        except OSError:
            continue
        if v == VENDOR and dv == DEVICE:
            return os.path.basename(d)
    return None


def open_port(mode: str):
    import serial
    if mode == "dtr-low":
        ser = serial.Serial()
        ser.port = DEV
        ser.baudrate = BAUD
        ser.timeout = 0.3
        # Deassert modem control lines before the port goes live.
        ser.dtr = False
        ser.rts = False
        ser.open()
        return ser
    # dtr-default: exactly how SerialConsole.open() does it today.
    return serial.Serial(DEV, BAUD, timeout=0.3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="dtr-default",
                    choices=["dtr-default", "dtr-low", "no-open"])
    ap.add_argument("--secs", type=float, default=120.0)
    ap.add_argument("--quiet-report", type=float, default=10.0,
                    help="report when a silent gap of this length occurs")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1

    print(f"[diag] mode={args.mode} secs={args.secs} dev={DEV} baud={BAUD}")
    print(f"[diag] PCIe before reset: {pcie_present()}")

    # Fire the FTDI POR reset.
    print("[diag] resetting Versal over FTDI ...")
    t0 = time.monotonic()
    rc = subprocess.run([sys.executable, RECONFIG, "--mode3-high"],
                        capture_output=True, text=True)
    treset = time.monotonic() - t0
    print(f"[diag] reset script rc={rc.returncode} took {treset:.1f}s")
    if rc.returncode != 0:
        print(rc.stdout)
        print(rc.stderr)
        return 1

    # Start the clock at end-of-reset (POR released ~here).
    t0 = time.monotonic()

    ser = None
    if args.mode != "no-open":
        topen = time.monotonic() - t0
        ser = open_port(args.mode)
        print(f"[diag] opened {DEV} at t+{topen:.2f}s (mode={args.mode}); "
              f"NOT writing anything")

    last_rx = None           # time of last serial byte
    first_rx = None
    max_gap = 0.0
    max_gap_at = 0.0
    pcie_at = None
    settled_at = None
    seen = set()
    line_buf = ""

    while time.monotonic() - t0 < args.secs:
        now = time.monotonic() - t0

        # Poll PCIe enumeration (independent of the serial port).
        if pcie_at is None and pcie_present():
            pcie_at = now
            print(f"[diag] *** PCIe endpoint ENUMERATED at t+{now:.1f}s "
                  f"(console settled: {settled_at}) ***")

        if ser is not None:
            data = ser.read(4096)
            now = time.monotonic() - t0
            if data:
                if first_rx is None:
                    first_rx = now
                    print(f"[diag] first serial byte at t+{now:.1f}s")
                if last_rx is not None:
                    gap = now - last_rx
                    if gap > max_gap:
                        max_gap, max_gap_at = gap, now
                last_rx = now
                settled_at = None  # activity resets the settle timer
                text = data.decode("utf-8", "replace")
                line_buf += text
                while "\n" in line_buf:
                    line, line_buf = line_buf.split("\n", 1)
                    line = line.replace("\r", "").rstrip()
                    for m in MARKERS:
                        if m in line and m not in seen:
                            seen.add(m)
                            print(f"[diag] t+{now:6.1f}s  marker {m!r}: {line[:100]!r}")
            else:
                # No data this read; check for a sustained quiet gap.
                if last_rx is not None and settled_at is None \
                        and (now - last_rx) >= args.quiet_report:
                    settled_at = last_rx
                    print(f"[diag] console QUIET for {args.quiet_report:.0f}s "
                          f"(last byte t+{last_rx:.1f}s); would be treated as idle. "
                          f"PCIe enumerated: {pcie_at}")
        else:
            time.sleep(0.3)

    print("\n[diag] ===== summary =====")
    print(f"[diag] first serial byte:   {first_rx}")
    print(f"[diag] last serial byte:    {last_rx}")
    print(f"[diag] max silent gap:      {max_gap:.1f}s at t+{max_gap_at:.1f}s")
    print(f"[diag] PCIe enumerated at:  {pcie_at}")
    print(f"[diag] markers seen:        {sorted(seen)}")
    reached_linux = any(m in seen for m in ("login:", "root@", "amd-ecp", "systemd"))
    print(f"[diag] reached Linux?       {reached_linux}")
    if not reached_linux and "Hit any key" in seen:
        print("[diag] >>> Board appears STUCK before Linux (likely halted at "
              "U-Boot). With mode=%s and zero writes, this implicates the OPEN "
              "itself if mode!=no-open.)" % args.mode)
    if ser is not None:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
