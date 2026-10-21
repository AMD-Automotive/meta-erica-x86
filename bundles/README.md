<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# Bundle authoring (`.rave2app` "image recipes")

Host-side tooling to **produce** `.rave2app` application bundles (and their
`appstore.json` catalog entries) from version-controlled recipes. Nothing here
runs on the board — it runs on a dev host.

The two files this tooling emits are described in full under
[`specification/`](specification/README.md):

- [`specification/rave2app-bundle-format.md`](specification/rave2app-bundle-format.md) —
  the on-device `.rave2app` bundle format (and its `appimage.yaml` descriptor)
  that the packer targets.
- [`specification/appstore-catalog-format.md`](specification/appstore-catalog-format.md) —
  the `appstore.json` catalog entry the packer renders and merges.

## Layout

```
bundles/
  _packer/            # the generic engine (shared by all recipes)
    make_bundle.py    #   driver: recipe.yaml -> .rave2app + catalog entry
    sources.py        #   source resolvers (file / script / url)
    catalog.py        #   appstore.json entry render + merge
  specification/      # the .rave2app + appstore.json format references
  <app-id>/           # one directory per app
    recipe.yaml       #   the "image recipe" (identity + slot + boot + components + catalog)
    device/ ...       #   component source material (build scripts, overlays, hooks, …)
  out/                # build output (gitignored): *.rave2app + *.appstore.json
```

## Build an app

```sh
python3 bundles/_packer/make_bundle.py bundles/<app-id>/recipe.yaml
```

This resolves every component `source`, computes per-member and whole-file
sha256s, renders + validates `appimage.yaml` (via the same `descriptor_tool.py`
the device uses, so authoring and runtime can never drift), packs the
`.rave2app` (reproducible tar, `appimage.yaml` first), and merges a real catalog
entry into a matching `appstore.json`.

Outputs land in `bundles/out/` (gitignored), both prefixed with the repo's short
git SHA so every commit packs to a unique, collision-free name:

- `<sha>_<id>[-<version>].rave2app` — the bundle.
- `<sha>_<id>[-<version>].appstore.json` — a catalog holding this bundle's entry.

A recipe with `version: auto` resolves its version to that same git SHA.

Useful flags:

| Flag | Effect |
|------|--------|
| `--web-base URL` | Base URL the catalog `url` is built from. Defaults to `https://rave2.cicdstorage.com/updates/bundles`. |
| `--out-dir DIR` | Where outputs are written. Defaults to `bundles/out`. |
| `--catalog FILE` | Merge the catalog entry into a specific file instead of the per-bundle default. |
| `--sha SHA` | Override the git SHA used in output names (default: this repo's `HEAD`). |
| `--print-name` | Print the resolved bundle file name and exit, without building. |
| `--keep-work` | Keep the staging directory for inspection. |

## The recipe

A `recipe.yaml` is the authoring input. Its identity and structural fields are
copied verbatim into the bundle's `appimage.yaml`; its `catalog:` block feeds
`appstore.json` and never enters the bundle. See
[`pcie-video-tester/recipe.yaml`](pcie-video-tester/recipe.yaml) for a worked
example.

| Field | Required | Feeds |
|-------|----------|-------|
| `id` | yes | `appimage.yaml` + catalog `id` + output file names |
| `name` | yes | `appimage.yaml` name + catalog `title` |
| `version` | yes | `appimage.yaml` + catalog + file names (`auto` → git SHA) |
| `vendor` | no | `appimage.yaml` + catalog |
| `description` | no | `appimage.yaml` |
| `requires` | no | `appimage.yaml` (e.g. `menu_min_version`) |
| `slot` | no | `appimage.yaml` (app-slot filesystem) |
| `boot` | no | `appimage.yaml` (boot-menu block) |
| `components` | yes | ordered list; each resolved to one bundle member |
| `catalog` | no | catalog-only fields — never enter the bundle |

Each entry in `components` needs a `kind` and a `source`. Optional `name`,
`target`, `stage`, and `required` are passed through into `appimage.yaml` (their
meanings are defined in the
[bundle format spec](specification/rave2app-bundle-format.md)).

## Publish (dev)

Copy the built bundle to where the catalog `url` points, and publish a single
`appstore.json` the board can fetch. To accumulate multiple apps into one store
catalog, point `--catalog` at a shared file when you build (each app is merged in
by `id`):

```sh
python3 bundles/_packer/make_bundle.py bundles/<app-id>/recipe.yaml \
    --catalog bundles/out/appstore.json

cp bundles/out/*_<app-id>*.rave2app  /scratch2/shared-downloads/artifactory/ad-hoc/copilot/bundles/
cp bundles/out/appstore.json         /scratch2/shared-downloads/artifactory/ad-hoc/copilot/appstore.json
```

Keep each `.rave2app`'s name unchanged when you copy it — the catalog `url` is
built from that exact (SHA-prefixed) file name. Then install it from the board's
App Store.

## Recipe `source` types

| `type` | Obtains the member by | Fields |
|--------|-----------------------|--------|
| `file`   | copying a local artifact (e.g. a CI output placed under the recipe dir) | `from`, `produces`, `gzip?` |
| `script` | running a build script that writes the member | `run`, `produces` (script env: `RECIPE_DIR`, `OUT_DIR`, `VERSION`, `WORK_DIR`) |
| `url`    | downloading an http(s) artifact | `from`, `produces`, `sha256?`, `gzip?` |

`gzip: true` gzips the resolved member (used for `versal-*` when the source is
not already `.gz`). Versal artifacts that are already `.gz` are stored as-is
(no `gzip:` flag).
