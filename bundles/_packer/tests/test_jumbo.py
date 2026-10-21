#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""A jumbo bundle declares it cannot fit the 50 GiB standard slot.

Both halves are pinned here: what the packer accepts and emits on the host, and
what the device's descriptor validator makes of the result. The rules that
matter are the ones that stop an app being installed into a space it cannot run
in - the minimum floor, the desired ceiling, and a layout whose fixed
partitions have to fit the floor because that is all some units will give.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import make_bundle  # noqa: E402

DEVICE = os.path.join(HERE, "..", "..", "..", "..",
                      "meta-buddy", "recipes-apps", "rave-menu", "files")
sys.path.insert(0, os.path.abspath(DEVICE))
import app_region  # noqa: E402
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


def recipe(**over):
    base = {
        "id": "big", "name": "Big", "version": "1",
        "type": "jumbo", "minimum_storage": 90, "desired_storage": 140,
        "components": [{"kind": "x86-rootfs-tar", "source": {"type": "file"}}],
        "boot": {"cmdline": {"kernel": "/boot/bzImage"}},
    }
    base.update(over)
    return base


def refused(fn, *args):
    """True if `fn` refuses — die() exits, validate() raises."""
    try:
        fn(*args)
    except (SystemExit, descriptor_tool.DescriptorError, app_region.RegionError):
        return True
    return False


def descriptor(**over):
    base = {
        "format": "rave2-appimage", "format_version": 3,
        "id": "big", "name": "Big", "version": "1",
        "type": "jumbo", "minimum_storage": 90, "desired_storage": 140,
        "components": [{"kind": "x86-rootfs-tar", "path": "x", "sha256": "a" * 64}],
        "boot": {"cmdline": {"kernel": "/boot/bzImage"}},
    }
    base.update(over)
    return base


# --- what the packer emits ---------------------------------------------------
out = make_bundle.render_appimage(recipe(), [("x", "a" * 64)])
check("a jumbo bundle declares format_version 3", out.get("format_version") == 3,
      str(out.get("format_version")))
check("type and both sizes reach the descriptor",
      (out.get("type"), out.get("minimum_storage"), out.get("desired_storage"))
      == ("jumbo", 90, 140), str(out))

plain = make_bundle.render_appimage(
    {"id": "t", "name": "T", "version": "1",
     "components": [{"kind": "x86-rootfs-tar"}],
     "boot": {"cmdline": {"kernel": "/boot/bzImage"}}},
    [("x", "a" * 64)])
check("a standard bundle stays on format_version 2",
      plain.get("format_version") == 2 and "type" not in plain, str(plain))

# --- what the packer refuses -------------------------------------------------
check("minimum_storage inside the standard slot is refused",
      refused(make_bundle.validate_app_type, recipe(minimum_storage=50)))
check("desired below minimum is refused",
      refused(make_bundle.validate_app_type, recipe(desired_storage=80)))
check("a fractional size is refused",
      refused(make_bundle.validate_app_type, recipe(minimum_storage=90.5)))
check("storage fields on a standard bundle are refused",
      refused(make_bundle.validate_app_type,
              recipe(type="standard", minimum_storage=90, desired_storage=140)))
big_layout = [{
    "kind": "x86-disk-image",
    "layout": [{"part": "app-boot", "size_mib": 102400},
               {"part": "app-root", "grow": True}],
}]
small_layout = [{
    "kind": "x86-disk-image",
    "layout": [{"part": "app-boot", "size_mib": 4096},
               {"part": "app-root", "grow": True}],
}]
check("a layout pinning more than the floor is refused",
      refused(make_bundle.validate_app_type, recipe(components=big_layout)))
check("a layout inside the floor is accepted",
      not refused(make_bundle.validate_app_type, recipe(components=small_layout)))

# --- what the device makes of it ---------------------------------------------
check("the device accepts a jumbo descriptor",
      not refused(descriptor_tool.validate, descriptor()))
check("the device refuses jumbo on format_version 2",
      refused(descriptor_tool.validate, descriptor(format_version=2)))
check("the device refuses a jumbo bundle with no sizes",
      refused(descriptor_tool.validate,
              descriptor(minimum_storage=None, desired_storage=None)))
check("the device refuses an unknown type",
      refused(descriptor_tool.validate, descriptor(type="enormous")))
check("type defaults to standard when absent",
      descriptor_tool.DEFAULT_APP_TYPE == "standard")

# --- sizing: minimum is a floor, desired a ceiling ---------------------------
GIB = 1024 * 1024 * 1024 // app_region.SECTOR


def region(gib):
    return {"kind": app_region.JUMBO, "first_sector": 2048,
            "last_sector": 2048 + gib * GIB - 1}


check("a tail below the minimum is refused",
      refused(app_region.negotiate_size, region(60), 90, 140))
check("a roomy tail is capped at desired",
      app_region.region_size_mib(
          app_region.negotiate_size(region(400), 90, 140)) == 140 * 1024)
check("a tight tail gets everything there is",
      app_region.region_size_mib(
          app_region.negotiate_size(region(100), 90, 140)) == 100 * 1024)
check("the user may take more than desired, up to the whole tail",
      app_region.region_size_mib(
          app_region.negotiate_size(region(400), 90, 140, 300)) == 300 * 1024)
check("a user size below the bundle minimum is refused",
      refused(app_region.negotiate_size, region(400), 90, 140, 60))

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
