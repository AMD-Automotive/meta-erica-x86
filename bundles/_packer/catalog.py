# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""catalog.py — render + merge an appstore.json entry for a built bundle.

The catalog is the App Store "strategy" layer (outer metadata only). It never
contains bundle internals — just enough to list, download, and integrity-check a
bundle. Schema mirrors docs/app-bundle-installer/examples/appstore.json:

    { "schema_version": 1, "apps": [ { id, title, version, short_description,
      description_long, vendor, thumbnail, size_bytes, url, sha256, has_versal,
      experimental }, ... ] }

The recipe's identity fields plus its `catalog:` block drive the entry; the
packer supplies url / size_bytes / sha256 / has_versal computed at build time.
"""

import json
import os

SCHEMA_VERSION = 1


def render_entry(recipe, url, size_bytes, whole_sha256, has_versal):
    """Build a single appstore.json app entry from a recipe + build outputs."""
    cat = recipe.get("catalog") or {}
    entry = {
        "id": recipe["id"],
        "title": recipe.get("name", recipe["id"]),
        "version": str(recipe["version"]),
        "short_description": cat.get("short_description", ""),
        "description_long": cat.get("description_long", ""),
        "vendor": recipe.get("vendor", ""),
        "thumbnail": cat.get("thumbnail", ""),
        "size_bytes": int(size_bytes),
        "url": url,
        "sha256": whole_sha256,
        "has_versal": bool(has_versal),
        "experimental": bool(cat.get("experimental", False)),
    }
    # Optional, and only on a jumbo bundle: lets the store warn that a unit has
    # not the room before it spends a long download finding out. The bundle
    # stays authoritative; these are a copy.
    if str(recipe.get("type") or "standard") == "jumbo":
        entry["jumbo"] = True
        entry["minimum_storage"] = int(recipe["minimum_storage"])
        entry["desired_storage"] = int(recipe["desired_storage"])
    return entry


def merge_into(catalog_path, entry):
    """Insert/replace `entry` (keyed by id) in the catalog at `catalog_path`."""
    data = {"schema_version": SCHEMA_VERSION, "apps": []}
    if os.path.isfile(catalog_path):
        try:
            with open(catalog_path) as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict) and isinstance(loaded.get("apps"), list):
                data = loaded
        except (ValueError, OSError):
            pass  # start fresh on a corrupt/unreadable catalog

    apps = [a for a in data.get("apps", []) if a.get("id") != entry["id"]]
    apps.append(entry)
    apps.sort(key=lambda a: a.get("id", ""))
    data["apps"] = apps
    data["schema_version"] = SCHEMA_VERSION

    os.makedirs(os.path.dirname(catalog_path) or ".", exist_ok=True)
    with open(catalog_path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
