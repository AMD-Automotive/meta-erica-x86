#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC, BM
# Maintainer: KC

"""Exercise sources.resolve_layout()'s per-member file/url sources.

Host-only; no target or external network (URL cases use a loopback HTTP server).
"""
import hashlib
import functools
import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def serve(directory):
    """Serve `directory` over http on a loopback port; returns (base_url, stop)."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=directory)
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd.shutdown


def run():
    tmp = tempfile.mkdtemp(prefix="layout-test.")
    try:
        recipe_dir = os.path.join(tmp, "recipe")
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "out")
        for d in (recipe_dir, work, out, os.path.join(recipe_dir, "device")):
            os.makedirs(d, exist_ok=True)

        p1 = b"BOOTFAT" * 1000
        p2 = b"\0" * 4096 + b"ROOTEXT4" * 500
        with open(os.path.join(recipe_dir, "device", "p1.fat"), "wb") as fh:
            fh.write(p1)
        with open(os.path.join(recipe_dir, "device", "p2.ext4"), "wb") as fh:
            fh.write(p2)

        # --- per-member file sources -------------------------------------
        comp = {
            "name": "app-disk",
            "kind": "x86-disk-image",
            "layout": [
                {"part": "app-boot", "produces": "x86/disk/p1.fat",
                 "source": {"type": "file", "from": "device/p1.fat"}},
                {"part": "app-root", "produces": "x86/disk/p2.ext4",
                 "source": {"type": "file", "from": "device/p2.ext4"}},
            ],
        }
        got = sources.resolve_layout(comp, recipe_dir, work, out, "1.0")
        check("file: two members resolved", got == [
            ("x86/disk/p1.fat", sha(p1)), ("x86/disk/p2.ext4", sha(p2))], got)
        check("file: member content staged",
              open(os.path.join(work, "x86/disk/p2.ext4"), "rb").read() == p2)

        # --- per-member url source, with sha256 --------------------------
        shutil.rmtree(work); os.makedirs(work)
        base, stop_server = serve(os.path.join(recipe_dir, "device"))
        url = base + "/p1.fat"
        comp_url = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [{"part": "app-root", "produces": "x86/disk/p1.fat",
                        "source": {"type": "url", "from": url, "sha256": sha(p1)}}],
        }
        try:
            got = sources.resolve_layout(comp_url, recipe_dir, work, out, "1.0")
            check("url: resolved and checksummed", got == [("x86/disk/p1.fat", sha(p1))], got)
        except sources.SourceError as exc:
            check("url: resolved and checksummed", False, exc)

        # --- url sha256 mismatch is refused ------------------------------
        # Cleared first: resolve_layout caches downloads in out_dir, and a stale
        # good copy there would satisfy this case without a fetch.
        shutil.rmtree(work); os.makedirs(work)
        shutil.rmtree(out); os.makedirs(out)
        comp_bad = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [{"part": "app-root", "produces": "x86/disk/p1.fat",
                        "source": {"type": "url", "from": url, "sha256": "0" * 64}}],
        }
        try:
            sources.resolve_layout(comp_bad, recipe_dir, work, out, "1.0")
            check("url: bad sha256 refused", False, "no error raised")
        except sources.SourceError:
            check("url: bad sha256 refused", True)

        # --- a non-http url is refused (no file:// reads) -----------------
        shutil.rmtree(work); os.makedirs(work)
        comp_file_url = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [{"part": "app-root", "produces": "x86/disk/p1.fat",
                        "source": {"type": "url",
                                   "from": "file://" + os.path.join(recipe_dir,
                                                                   "device", "p1.fat")}}],
        }
        try:
            sources.resolve_layout(comp_file_url, recipe_dir, work, out, "1.0")
            check("url: file:// refused", False, "no error raised")
        except sources.SourceError:
            check("url: file:// refused", True)
        stop_server()

        # --- the existing script form still works ------------------------
        shutil.rmtree(work); os.makedirs(work)
        script = os.path.join(recipe_dir, "make.sh")
        with open(script, "w") as fh:
            fh.write('#!/bin/sh\nset -eu\nprintf BOOTFAT > "$WORK_DIR/x86/disk/p1.fat"\n')
        comp_script = {
            "name": "app-disk", "kind": "x86-disk-image",
            "source": {"type": "script", "run": "make.sh"},
            "layout": [{"part": "app-root", "produces": "x86/disk/p1.fat"}],
        }
        got = sources.resolve_layout(comp_script, recipe_dir, work, out, "1.0")
        check("script: still supported", got == [("x86/disk/p1.fat", sha(b"BOOTFAT"))], got)

        # --- sizes ---------------------------------------------------------
        # A `grow` partition takes the region less the fixed entries, and the
        # device ignores any size_mib beside it, so the member is checked
        # against that budget rather than against size_mib.
        shutil.rmtree(work); os.makedirs(work)
        big = os.path.join(recipe_dir, "device", "big.ext4")
        with open(big, "wb") as fh:
            fh.write(b"\0" * (2 * 1024 * 1024))
        grown = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [{"part": "app-root", "produces": "x86/disk/big.ext4",
                        "size_mib": 1, "grow": True,
                        "source": {"type": "file", "from": "device/big.ext4"}}],
        }
        try:
            sources.resolve_layout(grown, recipe_dir, work, out, "1.0", region_mib=51200)
            check("grow: size_mib is not the limit", True)
        except sources.SourceError as exc:
            check("grow: size_mib is not the limit", False, exc)

        shutil.rmtree(work); os.makedirs(work)
        try:
            sources.resolve_layout(grown, recipe_dir, work, out, "1.0", region_mib=1)
            check("grow: refused past the region budget", False, "no error raised")
        except sources.SourceError:
            check("grow: refused past the region budget", True)

        shutil.rmtree(work); os.makedirs(work)
        fixed = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [{"part": "app-boot", "produces": "x86/disk/big.ext4",
                        "size_mib": 1,
                        "source": {"type": "file", "from": "device/big.ext4"}}],
        }
        try:
            sources.resolve_layout(fixed, recipe_dir, work, out, "1.0", region_mib=51200)
            check("fixed: member past size_mib refused", False, "no error raised")
        except sources.SourceError:
            check("fixed: member past size_mib refused", True)

        # Exact fits are valid, but zero remaining space is not an unknown
        # budget. Include a fixed member so the grow budget must subtract it.
        mixed_sizes = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [
                {"part": "app-boot", "produces": "x86/disk/p1.fat",
                 "size_mib": 1,
                 "source": {"type": "file", "from": "device/p1.fat"}},
                grown["layout"][0],
            ],
        }
        for budget, accepted in ((3, True), (2, False), (1, False),
                                 (0, False), (-1, False), (None, True)):
            shutil.rmtree(work); os.makedirs(work)
            try:
                sources.resolve_layout(mixed_sizes, recipe_dir, work, out,
                                       "1.0", region_mib=budget)
                got_accepted = True
            except sources.SourceError:
                got_accepted = False
            check(f"mixed layout: region {budget!r} MiB",
                  got_accepted == accepted, f"accepted={got_accepted}")

        # A fixed-only layout can exceed the region even when each member fits
        # its own partition declaration.
        shutil.rmtree(work); os.makedirs(work)
        fixed["layout"][0]["size_mib"] = 2
        try:
            sources.resolve_layout(fixed, recipe_dir, work, out, "1.0", region_mib=1)
            check("fixed: layout past region budget refused", False, "no error raised")
        except sources.SourceError:
            check("fixed: layout past region budget refused", True)

        # --- rejections ---------------------------------------------------
        shutil.rmtree(work); os.makedirs(work)
        mixed = {
            "name": "app-disk", "kind": "x86-disk-image",
            "source": {"type": "script", "run": "make.sh"},
            "layout": [{"part": "app-root", "produces": "x86/disk/p1.fat",
                        "source": {"type": "file", "from": "device/p1.fat"}}],
        }
        try:
            sources.resolve_layout(mixed, recipe_dir, work, out, "1.0")
            check("script + per-member refused", False, "no error raised")
        except sources.SourceError:
            check("script + per-member refused", True)

        partial = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [
                {"part": "app-boot", "produces": "x86/disk/p1.fat",
                 "source": {"type": "file", "from": "device/p1.fat"}},
                {"part": "app-root", "produces": "x86/disk/p2.ext4"},
            ],
        }
        try:
            sources.resolve_layout(partial, recipe_dir, work, out, "1.0")
            check("member with no source refused", False, "no error raised")
        except sources.SourceError:
            check("member with no source refused", True)

        unsafe = {
            "name": "app-disk", "kind": "x86-disk-image",
            "layout": [{"part": "app-root", "produces": "../escape.bin",
                        "source": {"type": "file", "from": "device/p1.fat"}}],
        }
        try:
            sources.resolve_layout(unsafe, recipe_dir, work, out, "1.0")
            check("unsafe produces refused", False, "no error raised")
        except sources.SourceError:
            check("unsafe produces refused", True)

        comp_level_file = {
            "name": "app-disk", "kind": "x86-disk-image",
            "source": {"type": "file", "from": "device/p1.fat", "produces": "x/y"},
            "layout": [{"part": "app-root", "produces": "x86/disk/p1.fat"}],
        }
        try:
            sources.resolve_layout(comp_level_file, recipe_dir, work, out, "1.0")
            check("component-level non-script refused", False, "no error raised")
        except sources.SourceError:
            check("component-level non-script refused", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
