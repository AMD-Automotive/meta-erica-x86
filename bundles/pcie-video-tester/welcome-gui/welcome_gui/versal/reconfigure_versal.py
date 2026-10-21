#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Reconfigure the Versal via FTDI-controlled POR_B reset.

Board: Rave2 (FT4232HQ)

Vendored verbatim from the rave-menu app (recipes-utils/files/reconfigure_versal.py)
so the Welcome GUI can reboot the Versal when its PCIe endpoint is missing. Keep
this in sync with the source of truth in rave-menu.

Schematic pin mapping:
  Channel A (ADBUS): ADBUS6 → D38 Schottky → POR_B
  Channel D (DDBUS): DDBUS3 → R987 22Ω → Q12 gate (2N7002 NMOS)
    Q12 drain = MODE3, R517 10K pull-down on gate
    DDBUS3 LOW  → Q12 OFF → MODE3 stays HIGH (pull-up network)
    DDBUS3 HIGH → Q12 ON  → MODE3 pulled to GND

Sequence:
  1. Unbind ftdi_sio from channels A and D
  2. Drive DDBUS3 LOW → Q12 OFF → MODE3 stays HIGH (OSPI boot)
  3. Drive ADBUS6 LOW → assert POR_B via Schottky D38
  4. Hold 200ms
  5. Release ADBUS6 (hi-Z) → POR_B rises → Versal reconfigures
  6. Hold MODE3 100ms while boot mode latches, then release
  7. Re-bind ftdi_sio so ttyUSB devices return

Usage:
  reconfigure_versal [--mode3-high|--mode3-low]

  --mode3-high  (default) Hold MODE3 high during reset (Q12 OFF)
  --mode3-low   Drive MODE3 low during reset (Q12 ON) — JTAG boot mode
"""

import argparse
import glob
import os
import sys
import time


CH_A = 1  # Channel A = interface 1
CH_D = 4  # Channel D = interface 4

PORB_BIT = 0x40   # ADBUS6, bit 6
MODE3_BIT = 0x08  # DDBUS3, bit 3

FTDI_SIO_DRIVER = "/sys/bus/usb/drivers/ftdi_sio"
USB_DEVICES = "/sys/bus/usb/devices"

# The Rave2 board's FT4232H is identified by its USB *manufacturer* string
# (programmed into the FTDI EEPROM). Other FT4232H devices that may be plugged
# into the host — e.g. an "ADI LARK PORT BRIDGE", which reports manufacturer
# "FTDI" — enumerate with the same VID:PID (0403:6011), so we must NOT select by
# chip type alone or pyftdi errors with "N USB devices match URL". Matching on
# manufacturer picks the right cable regardless of which USB port it lands on,
# and works across boards (no per-unit serial is hardcoded). Case-insensitive.
FTDI_VID = "0403"
FT4232H_PID = "6011"
VERSAL_FTDI_MANUFACTURER = "Xilinx"


def _read_attr(path):
    try:
        with open(path, "r", errors="ignore") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def find_versal_ftdi():
    """Locate the FT4232H that controls the Versal POR_B / MODE3 lines.

    Returns ``(usb_dev, serial)`` where ``usb_dev`` is the sysfs USB device name
    (e.g. ``2-4``) used to scope the ftdi_sio unbind, and ``serial`` is that
    device's USB serial (used to address it unambiguously in the pyftdi URL).

    Selection prefers the FT4232H whose USB manufacturer matches
    ``VERSAL_FTDI_MANUFACTURER``. If none match but exactly one FT4232H is
    present, it is used (preserving the original single-cable behaviour). If the
    choice is ambiguous, a clear error is raised rather than acting on the wrong
    device.
    """
    candidates = []  # (usb_dev, serial, manufacturer)
    for path in sorted(glob.glob(os.path.join(USB_DEVICES, "*"))):
        usb_dev = os.path.basename(path)
        if ":" in usb_dev:  # interface node, not a device node
            continue
        if _read_attr(os.path.join(path, "idVendor")) != FTDI_VID:
            continue
        if _read_attr(os.path.join(path, "idProduct")) != FT4232H_PID:
            continue
        candidates.append((
            usb_dev,
            _read_attr(os.path.join(path, "serial")),
            _read_attr(os.path.join(path, "manufacturer")),
        ))

    if not candidates:
        raise RuntimeError(
            f"no FT4232H ({FTDI_VID}:{FT4232H_PID}) USB device found")

    preferred = [c for c in candidates
                 if VERSAL_FTDI_MANUFACTURER.lower() in c[2].lower()]
    if len(preferred) == 1:
        usb_dev, serial, _ = preferred[0]
        return usb_dev, serial
    if len(preferred) > 1:
        listing = ", ".join(f"{d} (sn={s!r})" for d, s, _ in preferred)
        raise RuntimeError(
            f"multiple '{VERSAL_FTDI_MANUFACTURER}' FT4232H devices found: "
            f"{listing}; cannot pick the Versal cable")
    if len(candidates) == 1:
        usb_dev, serial, _ = candidates[0]
        return usb_dev, serial

    listing = ", ".join(f"{d} (sn={s!r}, manuf={m!r})" for d, s, m in candidates)
    raise RuntimeError(
        f"cannot identify the Versal FT4232H among multiple FTDI devices: "
        f"{listing}; expected manufacturer '{VERSAL_FTDI_MANUFACTURER}'")


def _ftdi_url(serial, ch):
    """Build a pyftdi URL for a specific FT4232H channel.

    Uses the serial number to disambiguate when more than one FTDI is present;
    falls back to the plain chip-type URL when no serial is available (only safe
    when a single FT4232H is attached).
    """
    if serial:
        return f"ftdi://ftdi:4232:{serial}/{ch}"
    return f"ftdi://ftdi:4232/{ch}"


def unbind_interface(usb_dev, intf_num):
    """Unbind ftdi_sio from interface ``intf_num`` of a specific USB device.

    Scoped to ``usb_dev`` (e.g. ``2-4``) so we never disturb the interfaces of
    another FTDI device (such as the ADI bridge) that happens to be attached.
    """
    bintf = f"{usb_dev}:1.{intf_num}"
    if not os.path.exists(os.path.join(FTDI_SIO_DRIVER, bintf)):
        return None
    unbind = os.path.join(FTDI_SIO_DRIVER, "unbind")
    print(f"  Unbinding {bintf} from ftdi_sio")
    try:
        with open(unbind, "w") as f:
            f.write(bintf)
    except OSError as exc:
        print(f"  WARNING: could not unbind {bintf}: {exc}")
        return None
    return bintf


def rebind_interface(bintf):
    """Re-bind ftdi_sio to a previously unbound interface."""
    if not bintf:
        return
    bind = os.path.join(FTDI_SIO_DRIVER, "bind")
    try:
        with open(bind, "w") as f:
            f.write(bintf)
        print(f"  Rebound {bintf}")
    except OSError:
        pass  # kernel often auto-reclaims


def reconfigure(mode3_high=True):
    from pyftdi.ftdi import Ftdi

    usb_dev, serial = find_versal_ftdi()
    print(f"Using Versal FT4232H at USB {usb_dev} (serial {serial or 'n/a'})")

    ch_a_url = _ftdi_url(serial, CH_A)
    ch_d_url = _ftdi_url(serial, CH_D)

    # Unbind channels A (intf 0) and D (intf 3) of THIS device only
    print("Unbinding FTDI channels from kernel driver ...")
    intf_a = unbind_interface(usb_dev, 0)
    intf_d = unbind_interface(usb_dev, 3)
    time.sleep(0.5)

    try:
        # Configure MODE3 via Q12 gate
        if mode3_high:
            print("Driving DDBUS3 LOW → Q12 OFF → MODE3 HIGH (OSPI boot)")
            mode3_value = 0x00  # DDBUS3=0 → Q12 gate low → OFF
        else:
            print("Driving DDBUS3 HIGH → Q12 ON → MODE3 LOW (JTAG boot)")
            mode3_value = MODE3_BIT  # DDBUS3=1 → Q12 gate high → ON

        ftdi_d = Ftdi()
        ftdi_d.open_bitbang_from_url(ch_d_url, direction=MODE3_BIT)
        ftdi_d.write_data(bytes([mode3_value]))

        # Assert POR_B
        print("Asserting POR_B (ADBUS6 LOW through Schottky D38) ...")
        ftdi_a = Ftdi()
        ftdi_a.open_bitbang_from_url(ch_a_url, direction=PORB_BIT)
        ftdi_a.write_data(bytes([0x00]))

        # Hold reset
        print("Holding POR_B low for 200ms ...")
        time.sleep(0.2)

        # Release POR_B
        print("Releasing POR_B → Versal reconfiguration triggered")
        ftdi_a.open_bitbang_from_url(ch_a_url, direction=0x00)
        ftdi_a.write_data(bytes([0x00]))

        # Hold MODE3 while boot mode latches
        time.sleep(0.1)

        # Release MODE3
        ftdi_d.open_bitbang_from_url(ch_d_url, direction=0x00)
        ftdi_d.write_data(bytes([0x00]))
        print("MODE3 released")

        ftdi_a.close()
        ftdi_d.close()

        mode_str = "HIGH (OSPI)" if mode3_high else "LOW (JTAG)"
        print(f"\nVersal reconfiguration complete (MODE3={mode_str})")

    finally:
        print("Restoring kernel driver bindings ...")
        time.sleep(0.3)
        rebind_interface(intf_a)
        rebind_interface(intf_d)


def main():
    parser = argparse.ArgumentParser(
        description="Reconfigure the Versal via FTDI POR_B reset"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mode3-high", action="store_true", default=True,
        help="Hold MODE3 high during reset — OSPI boot (default)"
    )
    group.add_argument(
        "--mode3-low", action="store_true",
        help="Drive MODE3 low during reset — JTAG boot"
    )
    args = parser.parse_args()

    mode3_high = not args.mode3_low

    if os.geteuid() != 0:
        print("ERROR: must run as root (need sysfs access for ftdi_sio)", file=sys.stderr)
        sys.exit(1)

    reconfigure(mode3_high=mode3_high)


if __name__ == "__main__":
    main()
