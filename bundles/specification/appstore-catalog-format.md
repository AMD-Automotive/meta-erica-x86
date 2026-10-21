<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# The App Store Catalog — `appstore.json`

**Specification version: 1.0**

This is the file a RAVE2 device reads to populate its App Store. It's a plain
JSON document you host at a URL the device can reach over HTTP(S). When someone
opens the App Store on the device, it fetches this file, shows a card for every
app listed, and uses the details here to download and verify whatever the user
picks.

Think of the catalog as the *menu*. It tells the device what exists and where to
get it — nothing more. The heavy lifting (what actually gets installed, and how)
lives inside each bundle, which is a separate file described in
[the bundle specification](rave2app-bundle-format.md).

## The shape of the file

At the top level you have a small wrapper around a list of apps:

```json
{
  "schema_version": 1,
  "apps": [
    {
      "id": "pcie-video-tester",
      "title": "PCIe Video Tester",
      "version": "2026.06.25",
      "short_description": "PCIe video test (x86 + Versal)",
      "description_long": "Installs an Ubuntu-based x86 host onto the application slot and programs the Versal OSPI + UFS images.",
      "vendor": "AMD",
      "thumbnail": "https://store.example.com/thumbs/pcie-video-tester.png",
      "size_bytes": 1395800974,
      "url": "https://store.example.com/bundles/pcie-video-tester-2026.06.25.rave2app",
      "sha256": "fca436a675d8b73fb5ec662ddcd8f7f7aa9fcfe9bf1308549b06417554b5d35b",
      "has_versal": true,
      "experimental": true,
      "jumbo": false
    }
  ]
}
```

That's the whole format. Two top-level keys, and then one object per app.

## The top level

| Field | Type | Required | What it's for |
|-------|------|----------|---------------|
| `schema_version` | integer | yes | The catalog format version. It is `1` today. A device that doesn't understand a newer version is allowed to refuse the catalog, so don't invent your own numbers. |
| `apps` | array | yes | The list of apps to show. May be empty (an empty store is valid — the device will simply show nothing to install). |

## An app entry

Every object inside `apps` describes one installable application. All of the
following fields are expected on every entry.

| Field | Type | What it's for |
|-------|------|---------------|
| `id` | string | A stable, machine-friendly slug, e.g. `pcie-video-tester`. Use lowercase letters, digits, and hyphens. This is the identity the device tracks, so **keep it constant across versions** — changing it makes the device treat your app as a brand-new, unrelated one. It must be unique within the catalog. |
| `title` | string | The human name shown on the card, e.g. `PCIe Video Tester`. |
| `version` | string | The release version, shown to the user and used to tell builds apart. Always quote it so it stays a string — see the warning below. |
| `short_description` | string | A one-line summary for the card. Keep it tight. |
| `description_long` | string | A fuller paragraph for a details view. May be empty if you have nothing more to say. |
| `vendor` | string | Who publishes the app, e.g. `AMD`. |
| `thumbnail` | string (URL) | An image shown on the card. Host it at a URL the device can reach; PNG works well. If this URL is wrong or unreachable, the card renders blank — so check it. |
| `size_bytes` | integer | The exact size of the bundle file at `url`, in bytes. The device uses this to check it has room *before* downloading, so get it right (it's the real byte count of the `.rave2app`, not an estimate). |
| `url` | string (URL) | Where to download the bundle. Must be an absolute `http(s)://` URL with no embedded credentials. |
| `sha256` | string | The SHA-256 of the **whole bundle file** at `url`, as 64 hex characters. The device verifies the download against this, so it must match the published file byte-for-byte. |
| `has_versal` | boolean | `true` if the bundle programs a Versal device as part of installing. This is an advisory badge the device can use to set expectations (e.g. a longer install). |
| `experimental` | boolean | `true` to flag the app as not-for-production. Purely advisory — the device may show a warning. |

### Optional: jumbo applications

These three fields are optional and may be omitted entirely. They exist so the
store can tell a user that a unit hasn't the room for an app *before* it spends
a long download finding out. A device that doesn't know them ignores them, so
adding them doesn't change `schema_version`.

| Field | Type | What it's for |
|-------|------|---------------|
| `jumbo` | boolean | `true` if the bundle declares `type: jumbo` — it needs more than the standard 50 GiB application slot. Defaults to `false` when absent. |
| `minimum_storage` | integer | Whole GiB. Copy of the bundle's `minimum_storage`. The store may hide or grey out the app on a device that can't offer this much. |
| `desired_storage` | integer | Whole GiB. Copy of the bundle's `desired_storage`. Shown so the user can see what the app would take on a roomier unit. |

The **bundle is authoritative**: the device re-reads these values from
`appimage.yaml` and decides from those, so a catalog entry that disagrees only
misleads the user — it cannot make a too-small install happen. Keep the two in
step whenever you publish a new bundle. See
[Jumbo applications](rave2app-bundle-format.md#jumbo-applications) for what the
values mean and how the device picks a size.

### A real warning about `version`

Write versions like `2026.06.25` or `1.2.0`, and **always wrap them in quotes**
in the JSON. JSON itself won't misread a number, but the tooling that *produces*
catalogs has been bitten before by a value like `2026_06_25` being read as the
integer `20260625` upstream. Quoting the version everywhere — in your catalog and
in the bundle that feeds it — keeps the string you intended. If your version is
already a clean dotted string, you're fine.

## How the device uses all this

When the App Store opens, the device:

1. Downloads this catalog from its configured URL.
2. Shows one card per entry in `apps`, using `title`, `vendor`,
   `short_description`, and `thumbnail`.
3. When a user installs an app, it checks free space against `size_bytes`, downloads
   the file at `url`, and verifies it against `sha256` before doing anything
   irreversible. For an entry marked `jumbo`, it also checks the unit's jumbo
   reservation against `minimum_storage` first, so an app that could never
   install is never downloaded.

If the catalog can't be fetched, the store simply reports that it's offline —
there is no built-in fallback list on the device. So the availability of your
catalog URL *is* the availability of your store.

## Hosting more than one store

The device ships with one built-in catalog URL, but a user can add extra stores
on the device (App Store → **Manage app stores**). These extra stores persist
across reboots and can be removed at any time; the built-in one cannot.

Each store is entered as a **root** rather than a full catalog path. The device
resolves a root to a catalog by appending `appstore.json` when the root doesn't
already end in `.json`:

| What the user enters | What the device fetches |
|----------------------|-------------------------|
| `https://host/updates/` | `https://host/updates/appstore.json` |
| `https://host/updates` | `https://host/updates/appstore.json` |
| `https://host/my-catalog.json` | `https://host/my-catalog.json` |

The App Store fetches every configured store, merges their `apps`, and labels
each card with its source when more than one store is present. A store that is
unreachable is reported as offline on its own and never hides the others. Each
catalog still follows exactly the format described above — nothing about a
single catalog file changes when it is one of several stores.

## Adding, updating, and removing apps

- **Add** an app by appending a new object to `apps`.
- **Update** an app by editing its entry in place — bump `version`, and update
  `url`, `size_bytes`, and `sha256` to point at the new bundle. Keep `id` the
  same so the device knows it's the same app.
- **Remove** an app by deleting its entry. The download stops being offered;
  anything already installed on a device is unaffected.

Keep the file valid JSON throughout (a single stray comma breaks the whole
store), and make sure every `url`, `thumbnail`, `size_bytes`, and `sha256`
actually matches the file you published. The most common store problems are a
mistyped URL, a stale `sha256` after re-uploading a bundle, or a `size_bytes`
that no longer matches the file.

## Changelog

- **1.0** — First tracked version, matching the catalog's `schema_version: 1`.
