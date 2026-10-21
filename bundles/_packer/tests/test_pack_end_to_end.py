#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Pack a real bundle through the CLI, the way a build actually does.

The unit tests all call render_appimage directly, so none of them noticed when
the newly-enabled version gate made the packer refuse its own output: it
validates the rendered descriptor with descriptor_tool, and the build host has
no menu version for the gate to be satisfied by. This walks the whole path.
"""
import os
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKER = os.path.join(HERE, "..", "make_bundle.py")
PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


RECIPE = """recipe_version: 1
id: smoke-test
name: Smoke Test
version: "1.0.0"
vendor: Test
slot:
  filesystem: ext4
  label: app-a
  mkfs_options: "-O ^orphan_file"
components:
  - name: x86-rootfs
    kind: x86-rootfs-tar
    source:
      type: file
      from: device/rootfs.tar
      produces: x86/rootfs.tar
boot:
  label: Smoke Test
  make_default: false
  cmdline:
    kernel: /boot/vmlinuz
    ramdisk: /boot/initrd.img
"""

with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, "device"))
    with tarfile.open(os.path.join(tmp, "device", "rootfs.tar"), "w"):
        pass
    recipe_path = os.path.join(tmp, "recipe.yaml")
    open(recipe_path, "w").write(RECIPE)
    out = os.path.join(tmp, "out")

    r = subprocess.run(
        [sys.executable, PACKER, recipe_path, "--out-dir", out, "--compress-level", "1"],
        capture_output=True, text=True)
    check("the packer accepts its own output", r.returncode == 0,
          (r.stderr or r.stdout).strip()[-400:])

    if r.returncode == 0:
        bundles = [f for f in os.listdir(out) if f.endswith(".rave2app")]
        check("a bundle was produced", len(bundles) == 1, str(bundles))
        if bundles:
            with tarfile.open(os.path.join(out, bundles[0]), "r:gz") as tf:
                first = tf.getnames()[0]
                check("appimage.yaml is the first member", first == "appimage.yaml", first)
                text = tf.extractfile("appimage.yaml").read().decode()
            check("the declared boot survived into the descriptor",
                  "cmdline:" in text and "/boot/vmlinuz" in text)
            check("the version gate was applied", "menu_min_version" in text, text)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
