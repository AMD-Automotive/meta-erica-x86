#!/usr/bin/env python3
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""make_bundle.py — build a .rave2app bundle (+ catalog entry) from a recipe.

This is the host-side packer for the App Store. It turns a version-controlled
`recipe.yaml` ("image recipe") into:

  1. a finished `<id>-<version>.rave2app` bundle (a gzipped tar with
     `appimage.yaml` first), and
  2. a merged `appstore.json` catalog entry with real sha256s.

It deliberately never drives BitBake — recipe components point at CI output
artifacts (file/url) or host build scripts (script). See
docs/app-bundle-installer/phase-7-bundle-authoring.md.

Usage:
    make_bundle.py <recipe.yaml> [--out-dir DIR] [--catalog FILE]
                   [--web-base URL] [--keep-work]

The rendered `appimage.yaml` is validated with the *same* descriptor_tool.py the
device uses, so authoring and runtime can never drift on envelope rules.
"""

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

import catalog
import sources

try:
    import yaml
except ImportError:
    sys.stderr.write("make_bundle: PyYAML not available (pip install pyyaml)\n")
    sys.exit(3)

# --- Repo locations ----------------------------------------------------------
PACKER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(PACKER_DIR, "..", ".."))


def _find_descriptor_tool():
    """Locate the device's descriptor_tool.py so authoring and runtime validate
    with the same code. It normally lives in the sibling rave-menu layer."""
    candidates = [
        os.environ.get("RAVE2_DESCRIPTOR_TOOL", ""),
        os.path.join(REPO_ROOT, "recipes-apps", "rave-menu", "files", "descriptor_tool.py"),
        os.path.join(REPO_ROOT, "..", "meta-buddy", "recipes-apps", "rave-menu",
                     "files", "descriptor_tool.py"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return candidates[1]


DESCRIPTOR_TOOL = _find_descriptor_tool()

# Envelope constants the rendered appimage.yaml must carry (see 01-bundle-format).
FORMAT_TAG = "rave2-appimage"
# Bumped to 2 when declaring a boot became mandatory. requires.menu_min_version
# cannot police that on its own: menus older than 2026-09-08 carry the version
# comparator this release fixed, so they read the requirement as satisfied and
# install anyway, then boot the app on their own kernel arguments. Refusing an
# unfamiliar format_version is the one check those menus do perform correctly.
FORMAT_VERSION = 2
# A jumbo bundle declares it cannot fit the 50 GiB standard slot. Only those
# bundles declare 3, so an ordinary app still installs on a menu that predates
# jumbo support instead of being refused for a feature it does not use.
JUMBO_FORMAT_VERSION = 3
STANDARD_SLOT_GIB = 50
# Mirrors app_slots.APP_REGION on the device: the extent a standard app gets.
STANDARD_REGION_MIB = 51200
APP_TYPES = ("standard", "jumbo")
JUMBO_FIELDS = ("minimum_storage", "desired_storage")

# Component fields copied through verbatim from recipe -> appimage.yaml.
PASSTHROUGH_FIELDS = ("target", "stage", "required")
VERSAL_KINDS = ("versal-ospi", "versal-wic")


def die(msg):
    sys.stderr.write(f"make_bundle: ERROR: {msg}\n")
    sys.exit(1)


def load_recipe(path):
    with open(path) as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        die(f"recipe top level must be a mapping: {path}")
    for key in ("id", "name", "version"):
        if not data.get(key):
            die(f"recipe missing required field {key!r}")
    comps = data.get("components")
    if not isinstance(comps, list) or not comps:
        die("recipe 'components' must be a non-empty list")
    for i, c in enumerate(comps):
        if not isinstance(c, dict) or not c.get("kind"):
            die(f"component #{i} needs a 'kind'")
        if c.get("layout") and not isinstance(c["layout"], list):
            die(f"component #{i} 'layout' must be a list")
        # A layout may carry its own source per member instead of one for the
        # whole component; sources.resolve_layout() enforces which form is legal.
        if not c.get("source") and not c.get("layout"):
            die(f"component #{i} needs a 'source'")
    # Coerce identity to strings so a value like 2026_06_25 (which YAML parses as
    # the int 20260625) keeps its intended textual form in filenames + metadata.
    for key in ("id", "name", "version"):
        data[key] = str(data[key])
    require_boot_declaration(data)
    validate_app_type(data)
    return data


def app_type(recipe):
    return str(recipe.get("type") or "standard")


def is_jumbo(recipe):
    return app_type(recipe) == "jumbo"


def format_version_for(recipe):
    return JUMBO_FORMAT_VERSION if is_jumbo(recipe) else FORMAT_VERSION


def validate_app_type(recipe):
    """Check `type` and the jumbo storage declaration.

    Mirrors descriptor_tool._validate_app_type so a bundle is refused here, on
    the host, rather than after it has been published and downloaded.
    """
    kind = app_type(recipe)
    if kind not in APP_TYPES:
        die(f"unknown type {kind!r} (supported: {'/'.join(APP_TYPES)})")

    declared = [f for f in JUMBO_FIELDS if recipe.get(f) is not None]
    if kind != "jumbo":
        if declared:
            die(f"{', '.join(declared)} may only be set on a jumbo bundle; "
                f"add 'type: jumbo' or remove them")
        return

    sizes = {}
    for field in JUMBO_FIELDS:
        value = recipe.get(field)
        if value is None:
            die(f"a jumbo bundle must declare {field} (whole GiB)")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            die(f"{field} must be a whole number of GiB, got {value!r}")
        sizes[field] = value
    if sizes["minimum_storage"] <= STANDARD_SLOT_GIB:
        die(f"minimum_storage is {sizes['minimum_storage']} GiB, which fits the "
            f"{STANDARD_SLOT_GIB} GiB standard slot \u2014 ship this as a standard bundle")
    if sizes["desired_storage"] < sizes["minimum_storage"]:
        die(f"desired_storage ({sizes['desired_storage']} GiB) is below "
            f"minimum_storage ({sizes['minimum_storage']} GiB)")

    # Only the floor is guaranteed on a device, so everything pinned to a fixed
    # size has to fit inside it; the `grow` partition takes whatever more the
    # device was able to give.
    for i, comp in enumerate(recipe.get("components") or []):
        fixed = sum(int(p["size_mib"]) for p in (comp.get("layout") or [])
                    if not p.get("grow") and p.get("size_mib"))
        if fixed > sizes["minimum_storage"] * 1024:
            die(f"component #{i} layout pins {fixed} MiB, more than the "
                f"{sizes['minimum_storage']} GiB minimum_storage")


APP_IMAGE_KINDS = ("x86-rootfs-tar", "x86-rootfs-ext4", "custom-build",
                   "x86-disk-image")

# The menu release that learned to render boot.cmdline, and to read an entries
# file from inside a rootfs slot. An older menu does not understand either: it
# falls through to app_mode=direct and boots the app on the MENU's kernel
# arguments, silently and usually successfully — the exact failure this phase
# exists to remove. So a bundle that declares its boot refuses to install there.
#
# Applied to every declaring app, including a disk-image one adopting its own
# entry, which older menus did support: VAS3 is only published from this release
# onwards, so nothing is locked out and the rule stays a single sentence.
MENU_MIN_VERSION_DECLARED_BOOT = "2026-09-08"


def _ver_tuple(v):
    """Mirror of descriptor_tool._ver_tuple — the device is the source of truth.

    Stops at the build sha, which identifies a release rather than ordering it.
    """
    return tuple(int(g) for g in re.findall(r"\d+", str(v).split("_", 1)[0]))


def needs_declared_boot_menu(recipe):
    """Whether this bundle depends on the newer menu to boot as declared."""
    boot = recipe.get("boot") or {}
    return bool(boot.get("cmdline") or boot.get("entries_cfg"))


def require_boot_declaration(recipe):
    """An application must say how it wants to be booted.

    Without `boot.entries_cfg` or `boot.cmdline` the device falls back to its own
    menu's hypervisor and kernel arguments. That usually still boots, which is
    exactly the problem: the app looks healthy while running on somebody else's
    configuration, and changes when the menu is updated underneath it.
    """
    kinds = [c.get("kind") for c in recipe.get("components", [])]
    if not any(k in APP_IMAGE_KINDS for k in kinds):
        return
    boot = recipe.get("boot") or {}
    if boot.get("entries_cfg") or boot.get("cmdline"):
        return
    die(f"{recipe.get('id')} declares no boot contract.\n"
        "    It would inherit the destination menu's hypervisor and kernel\n"
        "    arguments, which are authored for the menu OS, not for this app.\n"
        "    Add boot.entries_cfg (adopt your own GRUB entry) or boot.cmdline\n"
        "    (declare the arguments) to say what this app actually needs.")


def git_sha(repo_dir, length=6):
    """Return the short git SHA of `repo_dir`'s HEAD, or None if unavailable."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, "-c", "safe.directory=*",
             "rev-parse", f"--short={length}", "HEAD"],
            capture_output=True, text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def resolve_version(recipe, sha):
    """Resolve a recipe `version` of `auto` to the git SHA (unique per commit)."""
    version = recipe["version"]
    if version.strip().lower() == "auto":
        if not sha:
            die("version: auto needs a git SHA \u2014 run inside a checkout or pass --sha")
        return sha
    return version


def bundle_filename(recipe_id, version, sha):
    """Bundle file name, prepended with the git SHA so each commit is unique.

    The SHA prefix is what guarantees a collision-free name in the store. When
    `version` already is the SHA (recipe `version: auto`), the id is used bare to
    avoid repeating the SHA; otherwise the version is kept as a suffix. A missing
    SHA (e.g. a non-git local build) falls back to the plain name.
    """
    stem = recipe_id if (sha and version == sha) else f"{recipe_id}-{version}"
    return f"{sha}_{stem}.rave2app" if sha else f"{stem}.rave2app"


def catalog_filename(bundle_name):
    """Catalog file name, paired 1:1 with the bundle so it carries the same git
    SHA prefix and never overwrites another commit's catalog in the store.

    e.g. ``8ac321_pcie-video-tester.rave2app`` -> ``8ac321_pcie-video-tester.appstore.json``.
    """
    stem = bundle_name[: -len(".rave2app")] if bundle_name.endswith(".rave2app") else bundle_name
    return f"{stem}.appstore.json"


def render_appimage(recipe, resolved):
    """Build the device appimage.yaml dict from the recipe + resolved members.

    `resolved` is a list parallel to recipe['components'], each a
    (member_path, sha256) tuple.
    """
    out = {"format": FORMAT_TAG, "format_version": format_version_for(recipe)}
    for key in ("id", "name", "version", "vendor", "description"):
        if recipe.get(key) is not None:
            out[key] = recipe[key]
    if is_jumbo(recipe):
        out["type"] = "jumbo"
        for key in JUMBO_FIELDS:
            out[key] = int(recipe[key])
    requires = dict(recipe.get("requires") or {})
    if needs_declared_boot_menu(recipe):
        have = requires.get("menu_min_version") or ""
        if _ver_tuple(MENU_MIN_VERSION_DECLARED_BOOT) > _ver_tuple(have):
            requires["menu_min_version"] = MENU_MIN_VERSION_DECLARED_BOOT
    if requires:
        out["requires"] = requires
    if recipe.get("slot"):
        out["slot"] = recipe["slot"]

    comps = []
    for comp, entry in zip(recipe["components"], resolved):
        if isinstance(entry, list):
            # Disk-image component: members are named inside `layout`, one per
            # partition, so there is no single path/sha256 pair.
            layout = []
            for part, (member, sha) in zip(comp["layout"], entry):
                item = {"part": part["part"], "path": member, "sha256": sha}
                for field in ("size_mib", "grow", "type", "filesystem"):
                    if field in part:
                        item[field] = part[field]
                layout.append(item)
            out_comp = {
                "name": comp.get("name", comp["kind"]),
                "kind": comp["kind"],
                "layout": layout,
            }
            for field in PASSTHROUGH_FIELDS:
                if field in comp:
                    out_comp[field] = comp[field]
            comps.append(out_comp)
            continue

        member, sha = entry
        entry_out = {
            "name": comp.get("name", comp["kind"]),
            "kind": comp["kind"],
            "path": member,
            "sha256": sha,
        }
        for field in PASSTHROUGH_FIELDS:
            if field in comp:
                entry_out[field] = comp[field]
        comps.append(entry_out)
    out["components"] = comps

    if recipe.get("boot"):
        out["boot"] = recipe["boot"]
    return out


def validate_descriptor(appimage_path, menu_version=""):
    if not os.path.isfile(DESCRIPTOR_TOOL):
        sys.stderr.write(
            f"make_bundle: WARNING: descriptor_tool.py not found at {DESCRIPTOR_TOOL}; "
            "skipping cross-validation\n"
        )
        return
    cmd = [sys.executable, DESCRIPTOR_TOOL, "validate", appimage_path]
    # The build host has no menu, and descriptor_tool fails closed on an unknown
    # one. Answer the question that is actually meaningful here: would a device
    # new enough to meet our own stated requirement accept this bundle?
    if menu_version:
        cmd += ["--menu-version", menu_version]
    rc = subprocess.call(cmd)
    if rc != 0:
        die("rendered appimage.yaml failed descriptor_tool validation (see above)")


def _reset_tarinfo(ti):
    """Normalise a TarInfo for byte-reproducible archives."""
    ti.mtime = 0
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if ti.isdir():
        ti.mode = 0o755
    else:
        ti.mode = 0o755 if (ti.mode & 0o100) else 0o644
    return ti


def pack_bundle(appimage_yaml_text, work_dir, member_order, out_path, compress_level=9):
    """Write the gzipped tar: appimage.yaml first, then members in order."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # Stream tar -> gzip -> disk. Buffering the tar first would cost as much RAM
    # as the bundle is big, which a captured disk image (tens of GB) does not fit
    # in. The explicit GzipFile keeps mtime=0, so the output stays reproducible.
    with gzip.GzipFile(out_path, "wb", compresslevel=compress_level, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            # appimage.yaml MUST be first (device reads it from the head).
            data = appimage_yaml_text.encode("utf-8")
            ti = tarfile.TarInfo("appimage.yaml")
            ti.size = len(data)
            _reset_tarinfo(ti)
            tar.addfile(ti, io.BytesIO(data))
            for member in member_order:
                abs_path = os.path.join(work_dir, member)
                ti = tar.gettarinfo(abs_path, arcname=member)
                _reset_tarinfo(ti)
                with open(abs_path, "rb") as fh:
                    tar.addfile(ti, fh)


def sha256_path(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a .rave2app bundle from a recipe.")
    ap.add_argument("recipe", help="path to recipe.yaml")
    ap.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "bundles", "out"))
    ap.add_argument("--catalog", default=None, help="catalog json (default <out-dir>/<sha>_<id>.appstore.json)")
    ap.add_argument(
        "--web-base",
        default="https://rave2.cicdstorage.com/updates/bundles",
        help="base URL the catalog 'url' is built from",
    )
    ap.add_argument("--keep-work", action="store_true", help="keep the staging dir")
    ap.add_argument(
        "--compress-level", type=int, default=9, choices=range(1, 10),
        metavar="1-9",
        help="gzip level (default 9). The device does not care — any level yields "
             "the same bytes once decompressed — so drop it for a large capture, "
             "where level 9 costs an hour for a percent or two of size.")
    ap.add_argument(
        "--sha",
        default=None,
        help="git SHA to prepend to the bundle name (default: HEAD of this repo)",
    )
    ap.add_argument(
        "--print-name",
        action="store_true",
        help="print the resolved bundle file name and exit (no build)",
    )
    args = ap.parse_args(argv)

    recipe_path = os.path.abspath(args.recipe)
    recipe_dir = os.path.dirname(recipe_path)
    recipe = load_recipe(recipe_path)

    # The git SHA prefixes the bundle name so every commit packs to a unique
    # file in the store (and resolves a `version: auto` recipe).
    sha = args.sha or git_sha(REPO_ROOT)
    recipe["version"] = resolve_version(recipe, sha)
    bundle_name = bundle_filename(recipe["id"], recipe["version"], sha)

    if args.print_name:
        print(bundle_name)
        return

    out_dir = os.path.abspath(args.out_dir)
    catalog_path = args.catalog or os.path.join(out_dir, catalog_filename(bundle_name))
    os.makedirs(out_dir, exist_ok=True)

    work_dir = tempfile.mkdtemp(prefix="rave2app-pack.")
    try:
        # 1. Resolve every component source into a staged member + sha256.
        resolved = []
        member_order = []
        has_versal = False
        print(f"==> resolving {len(recipe['components'])} component(s)")
        # What the device will have room for: the fixed region, or the floor a
        # jumbo bundle declares.
        region_mib = (int(recipe["minimum_storage"]) * 1024 if is_jumbo(recipe)
                      else STANDARD_REGION_MIB)
        for comp in recipe["components"]:
            if comp.get("layout"):
                parts = sources.resolve_layout(
                    comp, recipe_dir, work_dir, out_dir, recipe["version"],
                    region_mib=region_mib,
                )
                resolved.append(parts)
                for member, sha in parts:
                    member_order.append(member)
                    print(f"    {comp['kind']:<16} {member}  sha256={sha[:12]}\u2026")
                continue
            member, sha = sources.resolve(
                comp, recipe_dir, work_dir, out_dir, recipe["version"]
            )
            resolved.append((member, sha))
            member_order.append(member)
            if comp["kind"] in VERSAL_KINDS:
                has_versal = True
            print(f"    {comp['kind']:<16} {member}  sha256={sha[:12]}…")

        # 2. Render + validate appimage.yaml.
        appimage = render_appimage(recipe, resolved)
        appimage_text = yaml.safe_dump(appimage, sort_keys=False, default_flow_style=False)
        appimage_path = os.path.join(work_dir, "appimage.yaml")
        with open(appimage_path, "w") as fh:
            fh.write(appimage_text)
        print("==> validating rendered appimage.yaml with descriptor_tool.py")
        validate_descriptor(
            appimage_path,
            (appimage.get("requires") or {}).get("menu_min_version", ""))

        # 3. Pack the bundle (appimage.yaml first; reproducible tar+gzip).
        out_path = os.path.join(out_dir, bundle_name)
        print(f"==> packing {bundle_name}")
        pack_bundle(appimage_text, work_dir, member_order, out_path,
                    compress_level=args.compress_level)

        size = os.path.getsize(out_path)
        whole_sha = sha256_path(out_path)

        # 4. Render + merge the catalog entry.
        url = f"{args.web_base.rstrip('/')}/{bundle_name}"
        entry = catalog.render_entry(recipe, url, size, whole_sha, has_versal)
        catalog.merge_into(catalog_path, entry)

        print()
        print(f"    bundle : {out_path}")
        print(f"    size   : {size} bytes")
        print(f"    sha256 : {whole_sha}")
        print(f"    catalog: {catalog_path} (entry id={entry['id']})")
        print(f"    url    : {url}")
    finally:
        if args.keep_work:
            print(f"    (kept staging dir: {work_dir})")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
