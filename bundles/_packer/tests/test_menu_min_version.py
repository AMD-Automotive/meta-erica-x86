#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""requires.menu_min_version must be set where, and only where, it is needed.

An older menu does not understand boot.cmdline, nor an entries file inside a
rootfs slot: it falls back to booting the app on the menu's own kernel
arguments. Setting the gate too narrowly reintroduces that silently. Setting it
too widely locks working bundles out of boards that would run them fine.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import make_bundle  # noqa: E402

MIN = make_bundle.MENU_MIN_VERSION_DECLARED_BOOT
PASS = FAIL = 0


def recipe(kind, boot, requires=None):
    r = {"id": "t", "name": "T", "version": "1",
         "components": [{"kind": kind, "path": "x", "sha256": "a" * 64}],
         "boot": boot}
    if requires:
        r["requires"] = requires
    return r


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}\n        expected {want!r}, got {got!r}")


def gate(r):
    resolved = [("x", "a" * 64)] * len(r["components"])
    out = make_bundle.render_appimage(r, resolved)
    return (out.get("requires") or {}).get("menu_min_version")


# --- needs the new menu ------------------------------------------------------
check("rootfs app declaring cmdline is gated",
      gate(recipe("x86-rootfs-tar", {"cmdline": {"kernel": "/boot/vmlinuz"}})), MIN)
check("custom-build declaring cmdline is gated",
      gate(recipe("custom-build", {"cmdline": {"kernel": "/boot/vmlinuz"}})), MIN)
check("rootfs app declaring entries_cfg is gated",
      gate(recipe("x86-rootfs-tar", {"entries_cfg": "/boot/grub.cfg"})), MIN)
check("disk-image app declaring cmdline is gated",
      gate(recipe("x86-disk-image", {"cmdline": {"kernel": "/bzImage"}})), MIN)
# Older menus did support this one, but VAS3 is only published from this release
# onwards, so the rule stays uniform rather than carrying a carve-out.
check("disk-image app adopting entries_cfg is gated too",
      gate(recipe("x86-disk-image", {"entries_cfg": "/isolinux/isolinux.cfg"})), MIN)

# --- does not need it --------------------------------------------------------
check("an app declaring nothing is not gated here (the packer refuses it instead)",
      gate(recipe("x86-rootfs-tar", {"label": "T"})), None)

# --- an author's own value ---------------------------------------------------
check("a higher explicit requirement is left alone",
      gate(recipe("x86-rootfs-tar", {"cmdline": {"kernel": "/boot/vmlinuz"}},
                  {"menu_min_version": "2027-01-01"})), "2027-01-01")
check("a lower explicit requirement is raised",
      gate(recipe("x86-rootfs-tar", {"cmdline": {"kernel": "/boot/vmlinuz"}},
                  {"menu_min_version": "2026-01-01"})), MIN)

# --- other requires keys survive ---------------------------------------------
r = recipe("x86-rootfs-tar", {"cmdline": {"kernel": "/boot/vmlinuz"}},
           {"something_else": "keep me"})
out = make_bundle.render_appimage(r, [("x", "a" * 64)])
check("unrelated requires keys are preserved",
      out["requires"].get("something_else"), "keep me")

# --- the recipe is not mutated ----------------------------------------------
r = recipe("x86-rootfs-tar", {"cmdline": {"kernel": "/boot/vmlinuz"}})
make_bundle.render_appimage(r, [("x", "a" * 64)])
check("the recipe dict is left untouched", r.get("requires"), None)

# --- version ordering matches the device ------------------------------------
check("the build sha does not affect ordering",
      make_bundle._ver_tuple("2026-09-08_5be68d"), (2026, 9, 8))
check("an older release sorts below the gate",
      make_bundle._ver_tuple("2026-09-07_075784") < make_bundle._ver_tuple(MIN), True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
