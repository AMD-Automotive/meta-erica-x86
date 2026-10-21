#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""pack_bundle() must stream, and must still be byte-for-byte reproducible.

The packer used to build the whole uncompressed tar in a BytesIO before gzipping,
which costs as much RAM as the bundle is big — 50 GiB for a captured disk image.
This checks the streaming replacement produces exactly the same bytes as the old
buffered implementation, so previously published bundles stay reproducible.
"""
import gzip
import io
import os
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import make_bundle  # noqa: E402


def pack_buffered(appimage_yaml_text, work_dir, member_order, out_path):
    """The previous implementation, kept here only as the reference."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        data = appimage_yaml_text.encode("utf-8")
        ti = tarfile.TarInfo("appimage.yaml")
        ti.size = len(data)
        make_bundle._reset_tarinfo(ti)
        tar.addfile(ti, io.BytesIO(data))
        for member in member_order:
            abs_path = os.path.join(work_dir, member)
            ti = tar.gettarinfo(abs_path, arcname=member)
            make_bundle._reset_tarinfo(ti)
            with open(abs_path, "rb") as fh:
                tar.addfile(ti, fh)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    raw.seek(0)
    with gzip.GzipFile(out_path, "wb", mtime=0) as gz:
        shutil.copyfileobj(raw, gz)


def main():
    tmp = tempfile.mkdtemp(prefix="pack-test.")
    failures = 0
    try:
        work = os.path.join(tmp, "work", "x86", "disk")
        os.makedirs(work)
        members = ["x86/disk/p1.fat", "x86/disk/p2.ext4"]
        payloads = [b"BOOT" * 5000, b"\0" * 200000 + b"ROOT" * 5000]
        for member, payload in zip(members, payloads):
            with open(os.path.join(tmp, "work", member), "wb") as fh:
                fh.write(payload)

        descriptor = "format: rave2-appimage\nformat_version: 1\nid: t\n"
        # Same basename in different directories: gzip stores the output filename
        # in its header, so comparing new.rave2app with old.rave2app would differ
        # on the name alone.
        os.makedirs(os.path.join(tmp, "a"))
        os.makedirs(os.path.join(tmp, "b"))
        new_path = os.path.join(tmp, "a", "t-1.0.rave2app")
        old_path = os.path.join(tmp, "b", "t-1.0.rave2app")
        work_dir = os.path.join(tmp, "work")
        make_bundle.pack_bundle(descriptor, work_dir, members, new_path)
        pack_buffered(descriptor, work_dir, members, old_path)

        new_bytes = open(new_path, "rb").read()
        old_bytes = open(old_path, "rb").read()
        if new_bytes == old_bytes:
            print("  PASS  streaming output is byte-identical to buffered")
        else:
            failures += 1
            print(f"  FAIL  outputs differ ({len(new_bytes)} vs {len(old_bytes)} bytes)")

        # appimage.yaml must still be the very first member.
        with tarfile.open(new_path, "r:gz") as tar:
            names = tar.getnames()
        if names and names[0] == "appimage.yaml":
            print("  PASS  appimage.yaml is first")
        else:
            failures += 1
            print(f"  FAIL  first member is {names[:1]}")
        if names[1:] == members:
            print("  PASS  members follow in recipe order")
        else:
            failures += 1
            print(f"  FAIL  member order is {names[1:]}")

        # The device reads the descriptor off the head of the stream without
        # pulling the whole bundle, so it must decompress from the front.
        with gzip.open(new_path, "rb") as gz:
            head = gz.read(2048)
        if b"appimage.yaml" in head and b"rave2-appimage" in head:
            print("  PASS  descriptor readable from the head of the stream")
        else:
            failures += 1
            print("  FAIL  descriptor not in the first 2 KiB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED' if failures else 'OK'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
