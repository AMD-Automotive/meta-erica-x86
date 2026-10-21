# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC, BM, TO
# Maintainer: KC

"""sources.py — resolve a recipe component's `source` into a bundle member.

Each component in a recipe.yaml carries a `source` describing how the packer
obtains the member that ships in the .rave2app. Three source types are
supported (BitBake is intentionally never driven here — point at CI artifacts):

    file    copy a local artifact (e.g. a CI output) into the member.
              from:     path relative to the recipe dir
              produces: in-tar member path (also the staged filename)
              gzip:     optional; gzip the copied file in place

    script  run a build script that writes the member.
              run:      script path relative to the recipe dir
              produces: in-tar member path the script must write under WORK_DIR
              (env: RECIPE_DIR, OUT_DIR, VERSION, WORK_DIR)

    url     download an http(s) artifact into the member.
              from:     http(s) URL
              produces: in-tar member path
              sha256:   optional; verify the download
              gzip:     optional; gzip the downloaded file in place

`produces` is the path inside the bundle tar (e.g. `versal/boot.bin.gz`). The
packer hashes the member exactly as stored.

A multi-member (disk-image) component uses `layout` instead, and may take its
members either from one `script` that writes them all, or from a `file`/`url`
source on each layout entry — see resolve_layout().
"""

import gzip
import hashlib
import os
import shutil
import subprocess
import urllib.request


class SourceError(Exception):
    """Raised when a component source cannot be resolved (message is user-facing)."""


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _gzip_in_place(path):
    """Gzip `path` -> `path` (keeps the same name; caller chose a .gz `produces`)."""
    tmp = path + ".gztmp"
    with open(path, "rb") as src, gzip.GzipFile(tmp, "wb", mtime=0) as dst:
        shutil.copyfileobj(src, dst)
    os.replace(tmp, path)


def _resolve_file(comp, src, recipe_dir, member_abs):
    frm = src.get("from")
    if not frm:
        raise SourceError(f"component {comp.get('name')!r}: file source needs 'from'")
    abs_from = frm if os.path.isabs(frm) else os.path.join(recipe_dir, frm)
    if not os.path.isfile(abs_from):
        raise SourceError(f"component {comp.get('name')!r}: file not found: {abs_from}")
    # A captured disk-image member runs to tens of GB, so staging it by copy costs
    # as much disk again. Hardlink instead where the filesystem allows: members
    # are read-only inputs, and the gzip path replaces the staged file rather
    # than writing through it.
    try:
        os.link(abs_from, member_abs)
        return
    except OSError:
        pass
    shutil.copyfile(abs_from, member_abs)


def _resolve_script(comp, src, recipe_dir, member_abs, work_dir, out_dir, version):
    run = src.get("run")
    if not run:
        raise SourceError(f"component {comp.get('name')!r}: script source needs 'run'")
    abs_run = run if os.path.isabs(run) else os.path.join(recipe_dir, run)
    if not os.path.isfile(abs_run):
        raise SourceError(f"component {comp.get('name')!r}: script not found: {abs_run}")
    env = dict(os.environ)
    env.update(
        RECIPE_DIR=recipe_dir,
        OUT_DIR=out_dir,
        VERSION=str(version),
        WORK_DIR=work_dir,
    )
    rc = subprocess.call(["/bin/sh", abs_run], env=env, cwd=recipe_dir)
    if rc != 0:
        raise SourceError(
            f"component {comp.get('name')!r}: build script exited {rc}: {abs_run}"
        )
    if not os.path.isfile(member_abs):
        raise SourceError(
            f"component {comp.get('name')!r}: script did not produce {member_abs}"
        )


# Component kinds whose URL downloads are cached in out_dir so that re-runs
# (or repeated local builds) skip the expensive re-download.
_CACHED_URL_KINDS = frozenset(("versal-ospi", "versal-wic"))


def _resolve_url(comp, src, member_abs, cache_dir=None):
    frm = src.get("from")
    if not frm or not (frm.startswith("http://") or frm.startswith("https://")):
        raise SourceError(
            f"component {comp.get('name')!r}: url source needs an http(s) 'from'"
        )

    want = src.get("sha256")

    # Check the download cache before hitting the network.
    cached_path = os.path.join(cache_dir, os.path.basename(frm)) if cache_dir else None
    if cached_path and os.path.isfile(cached_path):
        sha_ok = (not want) or (_sha256_file(cached_path) == want)
        if sha_ok:
            print(f"      (cached) {os.path.basename(frm)}")
            shutil.copyfile(cached_path, member_abs)
            return
        # sha256 mismatch — cached copy is stale, fall through to re-download.

    try:
        with urllib.request.urlopen(frm) as resp, open(member_abs, "wb") as out:
            shutil.copyfileobj(resp, out)
    except Exception as exc:  # network / HTTP errors surfaced cleanly
        raise SourceError(
            f"component {comp.get('name')!r}: download failed ({frm}): {exc}"
        )
    if want:
        got = _sha256_file(member_abs)
        if got != want:
            raise SourceError(
                f"component {comp.get('name')!r}: download sha256 mismatch "
                f"({frm}): expected {want}, got {got}"
            )

    # Persist the download to cache for future runs.
    if cached_path:
        os.makedirs(os.path.dirname(cached_path) or ".", exist_ok=True)
        shutil.copyfile(member_abs, cached_path)


def _stage_layout_member(comp, part, index, recipe_dir, work_dir, out_dir):
    """Resolve one layout entry that carries its own `source`."""
    src = part.get("source")
    stype = src.get("type")
    member_abs = os.path.join(work_dir, part["produces"])

    if stype == "file":
        _resolve_file(comp, src, recipe_dir, member_abs)
    elif stype == "url":
        _resolve_url(comp, src, member_abs, cache_dir=out_dir)
    else:
        raise SourceError(
            f"component {comp.get('name')!r}: layout #{index} "
            f"({part.get('part')}) has unknown source type {stype!r} (file/url)"
        )


def resolve_layout(comp, recipe_dir, work_dir, out_dir, version, region_mib=None):
    """Resolve a multi-member component (an exploded disk image).

    A disk-image component ships one raw member per partition, so its recipe
    carries a `layout` list with a `produces` per entry instead of a single
    `source.produces`. Two ways to supply the members:

      * a component-level `script` source, run once, that writes all of them
        (how a bundle built from a vendor image works); or
      * a `file` or `url` source on each layout entry (how a *captured* image
        works, where the members already exist and are pinned individually).

    Returns a list of (member_path, sha256) parallel to comp['layout'].
    With region_mib=None, only fixed member sizes can be checked.
    """
    layout = comp.get("layout")
    if not isinstance(layout, list) or not layout:
        raise SourceError(f"component {comp.get('name')!r}: 'layout' must be a non-empty list")

    src = comp.get("source")
    per_member = [i for i, part in enumerate(layout) if isinstance(part.get("source"), dict)]
    has_script = isinstance(src, dict) and src.get("type") == "script"

    if has_script and per_member:
        raise SourceError(
            f"component {comp.get('name')!r}: has both a 'script' source and "
            f"per-member sources (layout #{per_member[0]}) — use one or the other"
        )
    if not has_script:
        if src is not None:
            raise SourceError(
                f"component {comp.get('name')!r}: a component-level layout source "
                f"must be 'script' (got {src.get('type')!r}); per-member sources go "
                "on each layout entry"
            )
        missing = [i for i in range(len(layout)) if i not in per_member]
        if missing:
            raise SourceError(
                f"component {comp.get('name')!r}: layout #{missing[0]} "
                f"({layout[missing[0]].get('part')}) has no 'source', and the "
                "component has no 'script' source to write it"
            )

    for i, part in enumerate(layout):
        member = part.get("produces")
        if not member:
            raise SourceError(f"component {comp.get('name')!r}: layout #{i} needs 'produces'")
        if member.startswith("/") or ".." in member.split("/"):
            raise SourceError(
                f"component {comp.get('name')!r}: layout #{i} unsafe produces {member!r}"
            )
        os.makedirs(os.path.dirname(os.path.join(work_dir, member)), exist_ok=True)

    # A grow entry gets the region less every fixed entry; any size_mib beside
    # grow is ignored. None means the caller did not supply a region budget.
    # Zero means there is no room, so reject it before building/downloading.
    fixed_mib = sum(int(p["size_mib"]) for p in layout
                    if not p.get("grow") and p.get("size_mib"))
    grow_mib = None
    if region_mib is not None:
        region_mib = int(region_mib)
        if region_mib <= 0:
            raise SourceError(
                f"component {comp.get('name')!r}: region_mib must be positive"
            )
        if fixed_mib > region_mib:
            raise SourceError(
                f"component {comp.get('name')!r}: fixed partitions need "
                f"{fixed_mib} MiB but the region has {region_mib} MiB"
            )
        grow_mib = region_mib - fixed_mib
        if grow_mib == 0 and any(p.get("grow") for p in layout):
            raise SourceError(
                f"component {comp.get('name')!r}: fixed partitions leave no "
                "space for the grow partition"
            )

    if has_script:
        run = src.get("run")
        abs_run = run if os.path.isabs(run) else os.path.join(recipe_dir, run)
        if not os.path.isfile(abs_run):
            raise SourceError(f"component {comp.get('name')!r}: script not found: {abs_run}")
        env = dict(os.environ)
        env.update(
            RECIPE_DIR=recipe_dir,
            OUT_DIR=out_dir,
            VERSION=str(version),
            WORK_DIR=work_dir,
        )
        # The space the device will have for this layout: the standard region,
        # or a jumbo bundle's minimum_storage. A script that sizes filesystems
        # can check against it instead of hard-coding 50 GiB.
        if region_mib is not None:
            env["REGION_MIB"] = str(region_mib)
        rc = subprocess.call(["/bin/sh", abs_run], env=env, cwd=recipe_dir)
        if rc != 0:
            raise SourceError(
                f"component {comp.get('name')!r}: build script exited {rc}: {abs_run}"
            )
    else:
        for i, part in enumerate(layout):
            _stage_layout_member(comp, part, i, recipe_dir, work_dir, out_dir)

    out = []
    for part in layout:
        member_abs = os.path.join(work_dir, part["produces"])
        if not os.path.isfile(member_abs):
            raise SourceError(
                f"component {comp.get('name')!r}: no member produced for "
                f"{part.get('part')} ({part['produces']})"
            )
        # The device writes the member raw at the start of the partition, so a
        # member larger than its partition cannot be installed. A smaller
        # member fits; growing ext4 partitions are expanded on the device.
        if part.get("grow"):
            limit_mib, declared = grow_mib, f"grows into {grow_mib} MiB"
        else:
            size_mib = part.get("size_mib")
            limit_mib = int(size_mib) if size_mib is not None else None
            declared = f"declares size_mib: {limit_mib}"
        if limit_mib is not None:
            actual = os.path.getsize(member_abs)
            if actual > limit_mib * 1024 * 1024:
                raise SourceError(
                    f"component {comp.get('name')!r}: {part['produces']} is "
                    f"{actual // (1024 * 1024)} MiB but {part.get('part')} "
                    f"{declared}"
                )
        out.append((part["produces"], _sha256_file(member_abs)))
    return out


def resolve(comp, recipe_dir, work_dir, out_dir, version):
    """Resolve a component's source into a staged member file.

    Returns (member_path, sha256) where member_path is the in-tar path
    (e.g. 'versal/boot.bin.gz') and sha256 is of the member as stored.
    """
    src = comp.get("source")
    if not isinstance(src, dict):
        raise SourceError(f"component {comp.get('name')!r} has no 'source' mapping")
    stype = src.get("type")
    member = src.get("produces")
    if not member:
        raise SourceError(f"component {comp.get('name')!r}: source needs 'produces'")
    if member.startswith("/") or ".." in member.split("/"):
        raise SourceError(f"component {comp.get('name')!r}: unsafe produces {member!r}")

    member_abs = os.path.join(work_dir, member)
    os.makedirs(os.path.dirname(member_abs), exist_ok=True)

    if stype == "file":
        _resolve_file(comp, src, recipe_dir, member_abs)
    elif stype == "script":
        _resolve_script(comp, src, recipe_dir, member_abs, work_dir, out_dir, version)
    elif stype == "url":
        cache_dir = out_dir if comp.get("kind") in _CACHED_URL_KINDS else None
        _resolve_url(comp, src, member_abs, cache_dir=cache_dir)
    else:
        raise SourceError(
            f"component {comp.get('name')!r}: unknown source type {stype!r} "
            f"(file/script/url)"
        )

    if src.get("gzip"):
        _gzip_in_place(member_abs)

    return member, _sha256_file(member_abs)
