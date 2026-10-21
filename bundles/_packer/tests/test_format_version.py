#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""format_version 2 marks a bundle that declares how it boots.

It exists because requires.menu_min_version cannot police that alone: menus
older than 2026-09-08 carry the version comparator this release fixed, read the
requirement as satisfied, and install anyway - then boot the app on their own
kernel arguments. Refusing an unfamiliar format_version is the one check those
menus still perform correctly, so these tests pin both halves: what we emit, and
what each generation of device does with it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import make_bundle  # noqa: E402

DEVICE = os.path.join(HERE, "..", "..", "..", "..",
                      "meta-buddy", "recipes-apps", "rave-menu", "files")
sys.path.insert(0, os.path.abspath(DEVICE))
import descriptor_tool  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


def descriptor(fmt):
    return {"format": "rave2-appimage", "format_version": fmt,
            "id": "t", "name": "T", "version": "1",
            "components": [{"kind": "x86-rootfs-tar", "path": "x", "sha256": "a" * 64}],
            "boot": {"cmdline": {"kernel": "/boot/vmlinuz"}}}


# --- what the packer emits ---------------------------------------------------
check("the packer emits format_version 2", make_bundle.FORMAT_VERSION == 2,
      f"got {make_bundle.FORMAT_VERSION}")

recipe = {"id": "t", "name": "T", "version": "1",
          "components": [{"kind": "x86-rootfs-tar", "path": "x", "sha256": "a" * 64}],
          "boot": {"cmdline": {"kernel": "/boot/vmlinuz"}}}
out = make_bundle.render_appimage(recipe, [("x", "a" * 64)])
check("a rendered descriptor carries it", out.get("format_version") == 2, str(out.get("format_version")))

# --- what a current device accepts -------------------------------------------
for fmt in (1, 2):
    try:
        descriptor_tool.validate(descriptor(fmt), menu_version="2026-09-08_5be68d")
        ok = True
    except Exception as exc:  # noqa: BLE001 - the tool raises its own error type
        ok, err = False, exc
    check(f"a current device accepts format_version {fmt}", ok,
          "" if ok else str(err))

# 1 must stay accepted, or every bundle published before declarations became
# mandatory would stop installing on devices that can still run them.
check("format_version 1 is still supported", 1 in descriptor_tool.SUPPORTED_FORMAT_VERSIONS)
check("format_version 2 is supported", 2 in descriptor_tool.SUPPORTED_FORMAT_VERSIONS)

# --- what an older device does -----------------------------------------------
# Simulates the deployed 2026-09-07 menu, whose supported tuple is (1,).
saved = descriptor_tool.SUPPORTED_FORMAT_VERSIONS
descriptor_tool.SUPPORTED_FORMAT_VERSIONS = (1,)
try:
    try:
        descriptor_tool.validate(descriptor(2), menu_version="2026-09-07_075784")
        check("an older device REFUSES format_version 2", False,
              "it accepted the bundle")
    except Exception as exc:  # noqa: BLE001
        check("an older device REFUSES format_version 2",
              "format_version" in str(exc), str(exc))
    # And the reason this matters: on that same device the version gate does not
    # fire, because its comparator is the one this release fixed.
    try:
        descriptor_tool.validate(
            {**descriptor(1), "requires": {"menu_min_version": "2026-09-08"}},
            menu_version="2026-09-07_075784")
        gate_fired = False
    except Exception:  # noqa: BLE001
        gate_fired = True
    check("the version gate alone would not have stopped it (current comparator does)",
          gate_fired)
finally:
    descriptor_tool.SUPPORTED_FORMAT_VERSIONS = saved

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
